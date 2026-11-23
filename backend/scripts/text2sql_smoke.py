"""Smoke-test the Module A endpoint end to end, without starting the API.

The point of this script is the moment right after you change the Bedrock model
id: it answers "is the model reachable, and does the prompt format still
match?" in one command, and prints the exact prompt it sent plus the raw and
normalized SQL — so a bad answer can be diagnosed as a *format* problem rather
than guessed at.

    cd backend
    python scripts/text2sql_smoke.py                       # built-in sample schema
    python scripts/text2sql_smoke.py -q "total sales by region"
    python scripts/text2sql_smoke.py --dry-run             # print the prompt, call nothing

Credentials come from `backend/.env` / the environment (`ALIBI_TEXT2SQL_*`) — the
same config the running API uses, so a success here means the API will work too.
`--dry-run` needs no credentials and no credit: use it to inspect the prompt.

Reads nothing from and writes nothing to the database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/text2sql_smoke.py` from backend/ without installing the app.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas.datasets import (  # noqa: E402
    ColumnType,
    DatasetSchema,
    InferenceConfidence,
    SchemaColumn,
)
from app.services import text2sql  # noqa: E402
from app.services.text2sql import ddl, sql as sql_module  # noqa: E402

# Mirrors the `sales` fixture, so the printed DDL looks like what the app sends.
_SAMPLE_COLUMNS = [
    ("date", ColumnType.DATE),
    ("region", ColumnType.TEXT),
    ("product", ColumnType.TEXT),
    ("sales", ColumnType.NUMBER),
    ("units", ColumnType.WHOLE_NUMBER),
]

_DEFAULT_QUESTIONS = [
    "How many units were sold in total?",
    "total sales by region",
    "Which product had the highest sales?",
]


def sample_schema() -> DatasetSchema:
    return DatasetSchema(
        datasetId="sales",
        rowCount=12480,
        confidence=InferenceConfidence.HIGH,
        columns=[
            SchemaColumn(
                name=name,
                type=ctype,
                sampleValues="",
                included=True,
                confidence=InferenceConfidence.HIGH,
            )
            for name, ctype in _SAMPLE_COLUMNS
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-q",
        "--question",
        action="append",
        dest="questions",
        help="Question to ask (repeatable). Defaults to a built-in set.",
    )
    parser.add_argument(
        "--dataset-id",
        default="sales",
        help="Dataset id, which becomes the SQL table name (default: sales).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the prompt that would be sent and exit. No credentials needed.",
    )
    args = parser.parse_args()

    questions = args.questions or _DEFAULT_QUESTIONS
    schema = sample_schema()
    fmt = text2sql.prompt_format()
    context = [(args.dataset_id, schema)]  # one dataset; a folder would list several
    schema_ddl = ddl.render(context)

    print(f"prompt format : {fmt.name}")
    print(f"reads SQL as  : {fmt.sql_dialect_in}  ->  emits {sql_module.TARGET_DIALECT}")
    print(f"schema DDL    : {schema_ddl}")
    print()

    if args.dry_run:
        messages = fmt.build_messages(schema_ddl, questions[0])
        print("--- exact request body (messages) ---")
        for message in messages:
            print(f"[{message['role']}]")
            print(message["content"])
            print()
        return 0

    if not text2sql.is_configured():
        print(
            "ALIBI_TEXT2SQL_MODEL_ID is not set (a Bedrock model id; AWS credentials\n"
            "come from your normal profile). Set it in backend/.env (see\n"
            "backend/.env.example), or use --dry-run.",
            file=sys.stderr,
        )
        return 2

    failures = 0
    for question in questions:
        print(f"Q: {question}")
        try:
            result = text2sql.generate(question, context)
        except text2sql.LlmUnavailable as err:
            # Most likely: no AWS credentials, a model id this account cannot
            # invoke (AccessDenied), or a throttle.
            print(f"   UNREACHABLE: {err}", file=sys.stderr)
            failures += 1
            continue
        except text2sql.SqlRejected as err:
            print(f"   REJECTED BY GUARDRAIL: {err.message}")
            print(f"   reason: {err.detail}")
            failures += 1
            continue

        print(f"   raw        : {result.raw_sql}")
        if result.sql != result.raw_sql:
            print(f"   normalized : {result.sql}")
        print(f"   tokens     : {result.prompt_tokens} in / {result.completion_tokens} out")
        print()

    print(f"{len(questions) - failures}/{len(questions)} succeeded")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
