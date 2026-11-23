#!/usr/bin/env bash
# Download BIRD, Spider, and nvBench datasets and upload to S3.
#
# Usage:
#   AWS_PROFILE=alibi bash ml/data/download_datasets.sh <datasets-bucket>
#
# Get the bucket name from:
#   terraform -chdir=ml/terraform/envs/dev output datasets_bucket
#
# NOTE: Run this from your own terminal.
# NOTE: BIRD requires manual download from https://bird-bench.github.io/ (login required).
#       After downloading bird.zip, run: unzip bird.zip -d /tmp/bird

set -euo pipefail

DATASETS_BUCKET="${1:-}"
if [[ -z "$DATASETS_BUCKET" ]]; then
  echo "Usage: $0 <datasets-bucket-name>"
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

AWS_ARGS="--profile alibi --no-verify-ssl"

echo "=== Downloading Spider ==="
wget -q --show-progress -O "$TMP/spider.zip" \
  "https://drive.usercontent.google.com/download?id=1iRDVHLr4mX2wQKSgA9J8Pire73Jahh0m&export=download&confirm=t" \
  || { echo "[skip] Spider download failed — download manually from https://yale-nlp.github.io/spider/"; }

if [[ -f "$TMP/spider.zip" ]]; then
  unzip -q "$TMP/spider.zip" -d "$TMP"
  aws s3 sync "$TMP/spider" "s3://${DATASETS_BUCKET}/model_a/raw/spider/" $AWS_ARGS
  echo "[done] Spider → s3://${DATASETS_BUCKET}/model_a/raw/spider/"
fi

echo ""
echo "=== BIRD (manual) ==="
echo "BIRD requires registration at https://bird-bench.github.io/"
echo "After downloading, run:"
echo "  unzip bird.zip -d /tmp/bird"
echo "  aws s3 sync /tmp/bird s3://${DATASETS_BUCKET}/model_a/raw/bird/ $AWS_ARGS"

echo ""
echo "=== Downloading nvBench (Module C) ==="
wget -q --show-progress -O "$TMP/nvbench.zip" \
  "https://github.com/TsinghuaDatabaseGroup/nvBench/archive/refs/heads/main.zip" \
  || { echo "[skip] nvBench download failed"; }

if [[ -f "$TMP/nvbench.zip" ]]; then
  unzip -q "$TMP/nvbench.zip" -d "$TMP"
  aws s3 sync "$TMP/nvBench-main" "s3://${DATASETS_BUCKET}/model_c/raw/nvbench/" $AWS_ARGS
  echo "[done] nvBench → s3://${DATASETS_BUCKET}/model_c/raw/nvbench/"
fi

echo ""
echo "=== Next step: prepare instruction-tuning JSONL ==="
echo "  cd ml"
echo "  python -m src.model_a.prepare_data \\"
echo "    --spider-dir data/raw/spider \\"
echo "    --bird-dir   data/raw/bird \\"
echo "    --output-dir data/processed/model_a"
echo "  aws s3 sync data/processed/model_a s3://${DATASETS_BUCKET}/model_a/ $AWS_ARGS"
