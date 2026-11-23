"""`generate()` — the batching and out-of-memory handling the checkpoint evaluations rely on.

Runs without torch or a GPU (a stand-in module and a fake model), so it can sit in the offline suite:

    cd ML/src && python -m pytest ../tests -q
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class _OOM(Exception):
    pass


@pytest.fixture()
def eval_mod(monkeypatch):
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(OutOfMemoryError=_OOM, empty_cache=lambda: None)

    class _NoGrad:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    torch.inference_mode = lambda: _NoGrad()
    monkeypatch.setitem(sys.modules, "torch", torch)
    from model_a import eval_checkpoints
    return eval_checkpoints


class _Tok:
    eos_token_id, pad_token_id = 1, 0

    def convert_tokens_to_ids(self, _):
        return 2

    def __call__(self, texts, return_tensors=None, padding=None):
        ids = [list(range(len(t))) for t in texts]
        if return_tensors is None:
            return {"input_ids": ids}

        class _Batch(dict):
            def to(self, _):
                return self

        return _Batch(input_ids=types.SimpleNamespace(shape=(len(ids), max(map(len, ids)))))

    def decode(self, row, skip_special_tokens=True):
        return row


class _Model:
    """Out of memory above `limit` padded tokens; replies "ok" for every row."""

    device = "cpu"

    def __init__(self, limit):
        self.limit, self.calls, self.retried_inside_handler = limit, [], False
        self.failed_once = False

    def generate(self, input_ids, **kw):
        n, m = input_ids.shape
        self.calls.append((n, m))
        # A retry that runs inside an `except` block keeps the failed batch's tensors alive on a real GPU.
        if self.failed_once and sys.exc_info()[0] is not None:
            self.retried_inside_handler = True
        if n * (m + kw["max_new_tokens"]) > self.limit:
            self.failed_once = True
            raise _OOM()
        return ["ok"] * n


def _slice_ok(monkeypatch):
    # The fake decode receives "ok" and slices it by the prompt length; return it whole instead.
    return lambda self, row, skip_special_tokens=True: row


def test_batches_respect_the_token_budget_including_the_reply(eval_mod, monkeypatch):
    monkeypatch.setattr(_Tok, "decode", lambda self, row, skip_special_tokens=True: "ok")
    model = _Model(limit=10**9)
    prompts = ["x" * 2000] * 10 + ["x" * 100] * 100
    texts, unfit = eval_mod.generate(model, _Tok(), prompts, 64, 400, 20000)
    assert not unfit and all(t == "ok" for t in texts)
    assert max(n * (m + 400) for n, m in model.calls) <= 20000
    assert max(n for n, _ in model.calls) <= 64


def test_an_oom_batch_is_halved_and_every_prompt_still_gets_an_answer(eval_mod, monkeypatch):
    monkeypatch.setattr(_Tok, "decode", lambda self, row, skip_special_tokens=True: "ok")
    model = _Model(limit=3 * (1000 + 400))  # at most 3 of the long prompts at once, whatever the budget says
    texts, unfit = eval_mod.generate(model, _Tok(), ["x" * 1000] * 20, 64, 400, 10**9)
    assert not unfit and texts == ["ok"] * 20


def test_retries_do_not_run_inside_the_exception_handler(eval_mod, monkeypatch):
    monkeypatch.setattr(_Tok, "decode", lambda self, row, skip_special_tokens=True: "ok")
    model = _Model(limit=3 * (1000 + 400))
    eval_mod.generate(model, _Tok(), ["x" * 1000] * 20, 64, 400, 10**9)
    assert model.failed_once and not model.retried_inside_handler


def test_a_prompt_that_never_fits_is_reported_not_hidden(eval_mod, monkeypatch):
    monkeypatch.setattr(_Tok, "decode", lambda self, row, skip_special_tokens=True: "ok")
    model = _Model(limit=100)
    texts, unfit = eval_mod.generate(model, _Tok(), ["x" * 500] * 3, 64, 400, 10**9)
    assert sorted(unfit) == [0, 1, 2] and texts == ["", "", ""]
