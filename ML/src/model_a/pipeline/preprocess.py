"""
Pipeline stage 4/8 — preprocess.

Implements the one piece of Module A's data-format spec that
existed only on paper: schema truncation via a lexical-overlap schema-linking
heuristic ("Truncate schema to max 2048 tokens if a database has many
tables... include only tables/columns with lexical overlap to the
question" — Module A's data-format spec). prepare_data.py never
truncated long schemas; this stage does, then writes the instruction-ready
records split's input expects.

Token counts are approximated by whitespace-splitting (no tokenizer
dependency in this lightweight CPU stage) — close enough for a length gate.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import mlflow

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from model_a.pipeline._common import configure_mlflow, start_stage_run

_CREATE_TABLE_RE = re.compile(r"(CREATE TABLE .+?;)", re.IGNORECASE | re.DOTALL)
_WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


def _approx_tokens(text: str) -> int:
    return len(text.split())


def _split_tables(schema: str) -> list:
    """Split a multi-table DDL blob into individual CREATE TABLE statements,
    preserving anything before the first one (e.g. BIRD's external-knowledge comment)."""
    tables = _CREATE_TABLE_RE.findall(schema)
    return tables


def link_schema(schema: str, question: str, max_tokens: int) -> str:
    if _approx_tokens(schema) <= max_tokens:
        return schema

    tables = _split_tables(schema)
    if not tables:
        # Nothing structured to select from — fall back to a hard truncation.
        words = schema.split()
        return " ".join(words[:max_tokens])

    preamble = schema[: schema.find(tables[0])] if tables[0] in schema else ""
    q_words = {w.lower() for w in _WORD_RE.findall(question)}

    def overlap(table_ddl: str) -> int:
        table_words = {w.lower() for w in _WORD_RE.findall(table_ddl)}
        return len(q_words & table_words)

    scored = sorted(enumerate(tables), key=lambda it: overlap(it[1]), reverse=True)

    kept_idx = []
    budget = max_tokens - _approx_tokens(preamble)
    for idx, table_ddl in scored:
        cost = _approx_tokens(table_ddl)
        if budget - cost < 0 and kept_idx:
            # Always keep at least the single most relevant table.
            break
        kept_idx.append(idx)
        budget -= cost

    kept_idx.sort()  # restore original table order for readability
    return (preamble + "\n\n".join(tables[i] for i in kept_idx)).strip()


def main():
    args = parse_args()
    configure_mlflow()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    max_tokens = int(args.max_schema_tokens)

    records_path = Path(args.clean_dir) / "records.jsonl"
    records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line]

    with start_stage_run("preprocess"):
        mlflow.log_param("clean_dir", args.clean_dir)
        mlflow.log_param("max_schema_tokens", max_tokens)

        truncated_count = 0
        processed = []
        for r in records:
            linked = link_schema(r["schema"], r["question"], max_tokens)
            if linked != r["schema"]:
                truncated_count += 1
            processed.append({**r, "schema": linked})

        pct_truncated = round(100 * truncated_count / len(processed), 2) if processed else 0.0
        print(f"[preprocess] {len(processed):,} records, {truncated_count:,} schema-truncated ({pct_truncated}%)")
        mlflow.log_metric("record_count", len(processed))
        mlflow.log_metric("schema_truncated_count", truncated_count)
        mlflow.log_metric("schema_truncated_pct", pct_truncated)

        out_path = out / "records.jsonl"
        with open(out_path, "w", encoding="utf-8") as f:
            for r in processed:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        mlflow.log_artifact(str(out_path))
        print(f"[preprocess] wrote {out_path}")


def parse_args():
    p = argparse.ArgumentParser(description="Preprocess stage for Module A")
    p.add_argument("--clean-dir", default="data/clean/model_a")
    p.add_argument("--output-dir", default="data/preprocessed/model_a")
    p.add_argument("--max-schema-tokens", default="2048")
    return p.parse_args()


if __name__ == "__main__":
    main()
