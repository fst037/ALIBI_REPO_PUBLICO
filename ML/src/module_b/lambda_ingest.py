"""
Module B — Zero-ETL ingestion Lambda.

Triggered by S3 ObjectCreated events on the uploads/ prefix.

Pipeline:
  1. Download the uploaded file from S3
  2. Parse with pandas (CSV, Excel, JSON, Parquet)
  3. Build a column profile (name, dtype, n_unique, sample values)
  4. Call Claude API with function-calling to infer semantic schema
     (field descriptions, likely data type, is_primary_key hints)
  5. Generate column embeddings via Amazon Bedrock Titan Embeddings V2
  6. Store results to S3:
       s3://<artifacts_bucket>/module_b/<tenant_id>/<table_name>/schema.json
       s3://<artifacts_bucket>/module_b/<tenant_id>/<table_name>/embeddings.json

Environment variables (set by Terraform):
  ARTIFACTS_BUCKET, DATASETS_BUCKET, ANTHROPIC_SECRET_NAME, AWS_BEDROCK_REGION
"""

import json
import logging
import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_BEDROCK_MODEL_ID = "amazon.titan-embed-text-v2:0"
_MAX_SAMPLE_ROWS  = 5
_MAX_EMBED_CHARS  = 512


# ──────────────────────────────────────────────────────────────────────────────
# File parsing
# ──────────────────────────────────────────────────────────────────────────────

def _parse_file(local_path: str, suffix: str):
    """Parse uploaded file; returns a pandas DataFrame."""
    import pandas as pd
    suffix = suffix.lower()
    if suffix in (".csv",):
        return pd.read_csv(local_path, nrows=10_000)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(local_path, nrows=10_000)
    if suffix in (".json", ".jsonl"):
        try:
            return pd.read_json(local_path, lines=suffix == ".jsonl")
        except Exception:
            return pd.read_json(local_path)
    if suffix in (".parquet",):
        return pd.read_parquet(local_path)
    raise ValueError(f"Unsupported file type: {suffix}")


def _build_column_profile(df) -> List[Dict[str, Any]]:
    """Build a lightweight column profile for schema inference."""
    import pandas as pd
    profile = []
    for col in df.columns:
        series = df[col].dropna()
        n_unique = int(series.nunique())
        samples  = [str(v) for v in series.head(_MAX_SAMPLE_ROWS).tolist()]
        dtype    = str(df[col].dtype)
        # Rough Vega-Lite type hint
        if pd.api.types.is_numeric_dtype(df[col]):
            vl_type = "quantitative"
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            vl_type = "temporal"
        elif n_unique <= 20:
            vl_type = "nominal"
        else:
            vl_type = "nominal"
        profile.append({
            "name":     col,
            "dtype":    dtype,
            "vl_type":  vl_type,
            "n_unique": n_unique,
            "samples":  samples,
        })
    return profile


# ──────────────────────────────────────────────────────────────────────────────
# Schema inference via Claude API (function-calling)
# ──────────────────────────────────────────────────────────────────────────────

def _get_anthropic_key(secret_name: str) -> str:
    sm = boto3.client("secretsmanager")
    resp   = sm.get_secret_value(SecretId=secret_name)
    secret = json.loads(resp["SecretString"])
    return secret["api_key"]


def _infer_schema(column_profile: List[Dict], table_name: str, api_key: str) -> Dict:
    """Call Claude API with function-calling to produce semantic schema."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    schema_tool = {
        "name": "set_table_schema",
        "description": "Record the inferred semantic schema for a database table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_description": {
                    "type": "string",
                    "description": "One-sentence description of what this table represents.",
                },
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":        {"type": "string"},
                            "description": {"type": "string"},
                            "vl_type":     {"type": "string", "enum": ["quantitative", "nominal", "ordinal", "temporal"]},
                            "is_key":      {"type": "boolean"},
                        },
                        "required": ["name", "description", "vl_type"],
                    },
                },
            },
            "required": ["table_description", "columns"],
        },
    }

    col_text = "\n".join(
        f"- {c['name']} ({c['dtype']}, {c['n_unique']} unique values, samples: {c['samples'][:3]})"
        for c in column_profile
    )
    user_msg = (
        f"I have a database table called '{table_name}' with these columns:\n\n"
        f"{col_text}\n\n"
        "Please infer the semantic schema: what does this table represent, "
        "what does each column mean, what is the appropriate Vega-Lite data type, "
        "and which columns are likely primary/foreign keys?"
    )

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        tools=[schema_tool],
        tool_choice={"type": "tool", "name": "set_table_schema"},
        messages=[{"role": "user", "content": user_msg}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "set_table_schema":
            return block.input

    raise RuntimeError("Claude API did not return a schema tool call")


# ──────────────────────────────────────────────────────────────────────────────
# Embeddings via Bedrock Titan
# ──────────────────────────────────────────────────────────────────────────────

def _embed_columns(columns: List[Dict], bedrock_region: str) -> List[Dict]:
    """Generate Titan Embeddings V2 for each column description."""
    br = boto3.client("bedrock-runtime", region_name=bedrock_region)
    results = []
    for col in columns:
        text = f"{col['name']}: {col['description']}"[:_MAX_EMBED_CHARS]
        body = json.dumps({"inputText": text, "dimensions": 512, "normalize": True})
        resp  = br.invoke_model(modelId=_BEDROCK_MODEL_ID, body=body)
        embed = json.loads(resp["body"].read())["embedding"]
        results.append({"name": col["name"], "embedding": embed})
    logger.info("[ingest] embedded %d columns", len(results))
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Lambda handler
# ──────────────────────────────────────────────────────────────────────────────

def handler(event, context):
    artifacts_bucket  = os.environ["ARTIFACTS_BUCKET"]
    secret_name       = os.environ["ANTHROPIC_SECRET_NAME"]
    bedrock_region    = os.environ.get("AWS_BEDROCK_REGION", "us-east-1")

    s3 = boto3.client("s3")

    for record in event.get("Records", []):
        src_bucket = record["s3"]["bucket"]["name"]
        raw_key    = record["s3"]["object"]["key"]
        key        = urllib.parse.unquote_plus(raw_key)

        # uploads/<tenant_id>/<filename>
        parts = key.split("/")
        if len(parts) < 3:
            logger.warning("[ingest] unexpected key format: %s — skipping", key)
            continue

        tenant_id  = parts[1]
        filename   = parts[-1]
        suffix     = Path(filename).suffix
        table_name = Path(filename).stem

        logger.info("[ingest] processing s3://%s/%s", src_bucket, key)

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            local_path = tmp.name

        s3.download_file(src_bucket, key, local_path)

        try:
            df = _parse_file(local_path, suffix)
        except Exception as exc:
            logger.error("[ingest] parse failed for %s: %s", key, exc)
            continue

        logger.info("[ingest] parsed %d rows × %d cols", len(df), len(df.columns))
        col_profile = _build_column_profile(df)

        api_key = _get_anthropic_key(secret_name)
        schema  = _infer_schema(col_profile, table_name, api_key)

        embeddings = _embed_columns(schema["columns"], bedrock_region)

        # Merge vl_type from profile into schema columns (Claude may override)
        profile_map = {c["name"]: c for c in col_profile}
        for col in schema["columns"]:
            if col["name"] in profile_map and "vl_type" not in col:
                col["vl_type"] = profile_map[col["name"]]["vl_type"]

        # Store results
        base_prefix = f"module_b/{tenant_id}/{table_name}"
        s3.put_object(
            Bucket=artifacts_bucket,
            Key=f"{base_prefix}/schema.json",
            Body=json.dumps(schema, indent=2, ensure_ascii=False).encode(),
            ContentType="application/json",
        )
        s3.put_object(
            Bucket=artifacts_bucket,
            Key=f"{base_prefix}/embeddings.json",
            Body=json.dumps(embeddings, ensure_ascii=False).encode(),
            ContentType="application/json",
        )
        s3.put_object(
            Bucket=artifacts_bucket,
            Key=f"{base_prefix}/column_profile.json",
            Body=json.dumps(col_profile, ensure_ascii=False).encode(),
            ContentType="application/json",
        )

        logger.info("[ingest] stored schema + embeddings at s3://%s/%s/", artifacts_bucket, base_prefix)

    return {"status": "ok"}
