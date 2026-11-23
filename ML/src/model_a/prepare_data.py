"""
Data preparation for Module A — Text-to-SQL.

Converts BIRD (train) + Spider (train + train_others) datasets to instruction-tuning JSONL.
Output format per line: {"schema": "...", "question": "...", "sql": "...", "db_id": "..."}

Spider loading options (pick one):
  a) --spider-dir   path/to/raw/spider   — local raw Spider files (tables.json + train_spider.json)
  b) --spider-hf                         — auto-download b-mc2/sql-create-context from HuggingFace
                                           (includes Spider train + WikiSQL; no manual download needed)

BIRD still requires manual download from https://bird-bench.github.io/ (registration required).

Run once locally after downloading BIRD, then upload output to S3:
    python -m model_a.prepare_data \\
        --spider-hf \\
        --bird-dir   data/raw/bird \\
        --output-dir data/processed/model_a
    aws s3 sync data/processed/model_a s3://<datasets-bucket>/model_a/ --profile alibi

References:
  - Schema representation: DAIL-SQL conventions (Gao et al., 2024)
  - Sample rows for BIRD: mitigates dirty-data generalization gap (Li et al., 2023)
  - b-mc2/sql-create-context: Spider + WikiSQL pre-formatted with CREATE TABLE DDL

This module's loader functions (load_spider, load_bird, load_spider_hf,
_spider_ddl) are also reused by model_a/pipeline/{eda,clean,split}.py — the
MLflow pipeline (ML/README.md §10) that adds explicit cleaning, schema
truncation, and experiment tracking around this same extraction logic. Running
this script directly still works standalone exactly as below.
"""

import argparse
import json
from pathlib import Path
from typing import Iterator


def _spider_ddl(db: dict) -> str:
    """Build DDL string from Spider tables.json entry."""
    lines = []
    col_names = db["column_names_original"]   # [(table_idx, col_name), ...]
    col_types = db["column_types"]             # [type_str, ...]
    pk_cols = set(db.get("primary_keys", []))
    fk_map = {fk[0]: fk[1] for fk in db.get("foreign_keys", [])}

    for t_idx, table in enumerate(db["table_names_original"]):
        cols = []
        for c_idx, (tbl_idx, col_name) in enumerate(col_names):
            if tbl_idx != t_idx or col_name == "*":
                continue
            col_type = col_types[c_idx]
            pk = " PRIMARY KEY" if c_idx in pk_cols else ""
            fk = f" REFERENCES {db['table_names_original'][col_names[fk_map[c_idx]][0]]}({col_names[fk_map[c_idx]][1]})" if c_idx in fk_map else ""
            cols.append(f"  {col_name} {col_type}{pk}{fk}")
        lines.append(f"CREATE TABLE {table} (\n" + ",\n".join(cols) + "\n);")
    return "\n\n".join(lines)


def load_spider(spider_dir: Path) -> Iterator[dict]:
    """Yield samples from Spider train + train_others splits."""
    tables_path = spider_dir / "tables.json"
    if not tables_path.exists():
        print(f"[prepare] WARNING: {tables_path} not found — skipping Spider")
        return

    tables = {db["db_id"]: _spider_ddl(db) for db in json.loads(tables_path.read_text())}

    for split_file in ["train_spider.json", "train_others.json"]:
        path = spider_dir / split_file
        if not path.exists():
            continue
        for s in json.loads(path.read_text()):
            yield {
                "schema": tables.get(s["db_id"], ""),
                "question": s["question"],
                "sql": s["query"],
                "db_id": s["db_id"],
                "source": "spider",
            }


def _sqlite_ddl(db_path: Path) -> str:
    """CREATE TABLE statements of a SQLite file, as the model will see them at inference."""
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    finally:
        con.close()
    return "\n\n".join(r[0].strip().rstrip(";") + ";" for r in rows)


def _bird_samples(json_path: Path, db_root: Path, source: str) -> Iterator[dict]:
    """BIRD ships questions in one JSON and the schemas only inside the .sqlite files
    (``<db_root>/<db_id>/<db_id>.sqlite``); a sample's own record has no DDL."""
    ddl: dict = {}
    missing: set = set()
    for s in json.loads(json_path.read_text(encoding="utf-8")):
        db_id = s["db_id"]
        if db_id not in ddl and db_id not in missing:
            db_path = db_root / db_id / f"{db_id}.sqlite"
            if db_path.exists():
                ddl[db_id] = _sqlite_ddl(db_path)
            else:
                missing.add(db_id)
        if db_id in missing:
            continue  # no schema = a sample the model could only guess at; never train on it
        # Prepend external knowledge hint (helps with dirty data — Li et al. 2023)
        evidence = (s.get("evidence") or "").strip()
        schema = (f"-- External knowledge: {evidence}\n\n" if evidence else "") + ddl[db_id]
        yield {
            "schema": schema.strip(),
            "question": s["question"],
            "sql": s["SQL"],
            "db_id": db_id,
            "source": source,
        }
    if missing:
        print(f"[prepare] WARNING: no .sqlite for {len(missing)} BIRD db(s) under {db_root}: {sorted(missing)[:5]}")


def load_bird(bird_dir: Path) -> Iterator[dict]:
    """Yield samples from the BIRD train split (train/train.json + train/train_databases/)."""
    train_path = bird_dir / "train" / "train.json"
    if not train_path.exists():
        print(f"[prepare] WARNING: {train_path} not found — skipping BIRD")
        return
    yield from _bird_samples(train_path, bird_dir / "train" / "train_databases", "bird")


def load_bird_dev(bird_dir: Path) -> Iterator[dict]:
    """Yield BIRD dev (dev/dev.json + dev/dev_databases/) — the eval side of the EX gate."""
    dev_path = bird_dir / "dev" / "dev.json"
    if not dev_path.exists():
        print(f"[prepare] WARNING: {dev_path} not found — no BIRD dev in the eval split")
        return
    yield from _bird_samples(dev_path, bird_dir / "dev" / "dev_databases", "bird_dev")


def load_spider_hf() -> Iterator[dict]:
    """Download Spider training data from HuggingFace (b-mc2/sql-create-context).

    This dataset bundles Spider train + WikiSQL samples, pre-formatted with
    CREATE TABLE DDL in the 'context' field — same representation our prompt template uses.
    No Google Drive download or registration required.

    SSL verification is disabled to work behind the Zscaler TLS-inspection proxy.
    """
    try:
        from datasets import load_dataset
    except ImportError:
        print("[prepare] WARNING: 'datasets' not installed — run: pip install datasets")
        return

    import os
    import ssl
    import warnings

    # Zscaler TLS-inspection proxy — disable certificate verification everywhere
    os.environ["CURL_CA_BUNDLE"] = ""
    os.environ["REQUESTS_CA_BUNDLE"] = ""
    os.environ["SSL_CERT_FILE"] = ""
    ssl._create_default_https_context = ssl._create_unverified_context
    warnings.filterwarnings("ignore", message="Unverified HTTPS")

    # Monkey-patch httpx.Client so every instance created by huggingface_hub skips cert verification.
    # Must happen before the first HF request; configure_http_backend replaces an already-created
    # client and then closes it, causing "Cannot send a request, as the client has been closed."
    try:
        import httpx
        _orig_init = httpx.Client.__init__
        def _no_verify_init(self, *args, **kwargs):
            kwargs.setdefault("verify", False)
            _orig_init(self, *args, **kwargs)
        httpx.Client.__init__ = _no_verify_init

        _orig_async_init = httpx.AsyncClient.__init__
        def _no_verify_async_init(self, *args, **kwargs):
            kwargs.setdefault("verify", False)
            _orig_async_init(self, *args, **kwargs)
        httpx.AsyncClient.__init__ = _no_verify_async_init
    except ImportError:
        pass

    print("[prepare] downloading b-mc2/sql-create-context from HuggingFace ...")
    ds = load_dataset("b-mc2/sql-create-context", split="train")
    for row in ds:
        if not row.get("answer") or not row.get("question") or not row.get("context"):
            continue
        yield {
            "schema": row["context"].strip(),
            "question": row["question"].strip(),
            "sql": row["answer"].strip(),
            "db_id": "hf_spider",
            "source": "hf_sql_create_context",
        }


def write_jsonl(samples: list, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            if s.get("sql") and s.get("question") and s.get("schema"):
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
                count += 1
    return count


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)

    if args.spider_hf:
        print("[prepare] loading Spider via HuggingFace (b-mc2/sql-create-context) ...")
        spider_samples = list(load_spider_hf())
    else:
        print("[prepare] loading Spider from local files ...")
        spider_samples = list(load_spider(Path(args.spider_dir)))
    print(f"[prepare]   {len(spider_samples):,} Spider/HF samples")

    bird_samples: list = []
    if args.bird_dir:
        print("[prepare] loading BIRD ...")
        bird_samples = list(load_bird(Path(args.bird_dir)))
        print(f"[prepare]   {len(bird_samples):,} BIRD samples")
    else:
        print("[prepare] --bird-dir not provided — skipping BIRD (download from https://bird-bench.github.io/)")

    train_samples = spider_samples + bird_samples
    print(f"[prepare] total train: {len(train_samples):,}")

    n = write_jsonl(train_samples, output_dir / "train" / "train.jsonl")
    print(f"[prepare] wrote {n:,} -> {output_dir}/train/train.jsonl")

    # Eval split: the real dev sets (Spider dev + BIRD dev) when present — they have the databases
    # EX evaluation needs. Otherwise a 5% hold-out from the HF training data (loss tracking only).
    eval_samples: list = []
    spider_dev = Path(args.spider_dir) / "dev.json" if args.spider_dir else None
    tables_path = Path(args.spider_dir) / "tables.json" if args.spider_dir else None
    if spider_dev and spider_dev.exists() and tables_path and tables_path.exists():
        tables = {db["db_id"]: _spider_ddl(db) for db in json.loads(tables_path.read_text())}
        eval_samples += [
            {
                "schema": tables.get(s["db_id"], ""),
                "question": s["question"],
                "sql": s["query"],
                "db_id": s["db_id"],
                "source": "spider_dev",
            }
            for s in json.loads(spider_dev.read_text())
        ]
    if args.bird_dir:
        eval_samples += list(load_bird_dev(Path(args.bird_dir)))

    if eval_samples:
        n = write_jsonl(eval_samples, output_dir / "eval" / "eval.jsonl")
        by_source: dict = {}
        for e in eval_samples:
            by_source[e["source"]] = by_source.get(e["source"], 0) + 1
        print(f"[prepare] wrote {n:,} eval -> {output_dir}/eval/eval.jsonl {by_source}")
    elif spider_samples:
        # HF mode: carve 5% of HF data as a hold-out eval split (loss tracking only)
        cut = max(1, len(spider_samples) // 20)
        eval_samples = spider_samples[:cut]
        train_samples = spider_samples[cut:] + bird_samples
        # Rewrite train with the updated split
        n = write_jsonl(train_samples, output_dir / "train" / "train.jsonl")
        print(f"[prepare] rewrote {n:,} train samples (5% carved for eval)")
        n = write_jsonl(eval_samples, output_dir / "eval" / "eval.jsonl")
        print(f"[prepare] wrote {n:,} eval (HF hold-out, loss tracking only — not EX eval)")

    print("[prepare] done.")
    print(f"[prepare] next: aws s3 sync {output_dir} s3://<datasets-bucket>/model_a/ --profile alibi --no-verify-ssl")


def parse_args():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--spider-dir", default=None, help="Local Spider dataset root (contains tables.json)")
    g.add_argument("--spider-hf", action="store_true", help="Auto-download Spider from HuggingFace (b-mc2/sql-create-context)")
    p.add_argument("--bird-dir", default=None, help="Path to BIRD dataset root (contains train/). Optional but needed for BIRD training data.")
    p.add_argument("--output-dir", default="data/processed/model_a")
    return p.parse_args()


if __name__ == "__main__":
    main()
