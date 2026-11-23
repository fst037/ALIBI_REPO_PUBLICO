# Datasets

The raw datasets are committed in `ML/data/raw/` as their archives, stored with
**Git LFS** (~8.5 GB; unpacked they are ~42 GB, and several single files exceed
what Git LFS accepts). Install Git LFS before cloning, or run `git lfs pull`
afterwards, or you get small pointer files instead of the archives.

| File | Contents |
|---|---|
| `spider.zip` | Spider, unpacked layout (`spider/tables.json`, `spider/database/…`) |
| `bird_dev.zip` | BIRD dev pack, official archive (`dev_20240627/`) |
| `nvbench.zip` | nvBench (`nvbench/NVBench.json`, `nvbench/databases/…`, `nvbench/nvBench_VegaLite/`) |
| `bird_train.zip.part00` … `part04` | BIRD train pack, official archive split into < 2 GB parts |
| `SHA256SUMS.txt` | Checksums of every file and of the rejoined `bird_train.zip` |

To unpack into the layout the loaders expect (below), from the repository root:

```bash
cd ML/data/raw
sha256sum -c SHA256SUMS.txt --ignore-missing
cat bird_train.zip.part* > bird_train.zip        # Windows cmd: copy /b bird_train.zip.part* bird_train.zip

unzip spider.zip                                 # -> spider/
unzip nvbench.zip                                # -> nvbench/

mkdir -p bird && cd bird
unzip ../bird_train.zip -x '__MACOSX/*'          # -> train/train.json + train/train_databases.zip
(cd train && unzip train_databases.zip -x '__MACOSX/*' && rm train_databases.zip)
unzip ../bird_dev.zip && mv dev_20240627 dev     # -> dev/dev.json + dev/dev_databases.zip
(cd dev && unzip dev_databases.zip -x '__MACOSX/*' && rm dev_databases.zip)
```

The rejoined `bird_train.zip` and the unpacked `spider/`, `bird/` and `nvbench/`
folders are gitignored; only the archives above are committed.

Spider (Yu et al., 2018), BIRD (Li et al., 2023) and nvBench (Luo et al., 2021)
are redistributed under their original licenses; all credit to their authors.

The rest of this file is the original by-hand procedure from each dataset's
source, and the layout the loaders expect.

**Read the "What you actually need" table first.** Spider's databases and BIRD
together are tens of GB, and you do not need all of it to run a training job.

---

## What you actually need

| Want to... | Download | Roughly |
|---|---|---|
| Train | *nothing* — `b-mc2/sql-create-context` auto-downloads from HuggingFace | ~40 MB |
| Train on **real** Spider instead of the HF proxy | Spider: `tables.json`, `train_spider.json`, `train_others.json` | ~100 MB |
| Measure **real Spider execution accuracy** (`SPIDER_EX_TARGET = 0.70`) | Spider: `dev.json` + `database/` | ~1.5 GB |
| Train on BIRD as well | BIRD train pack | ~12 GB |
| Measure **real BIRD execution accuracy** (`BIRD_EX_TARGET = 0.45`) | BIRD dev pack | ~1.5 GB |
| Module C | nvBench | ~1 GB |

Sizes are approximate — check before starting a download on a slow link.

The current pipeline runs on the HuggingFace proxy dataset and produces a
**seeded 5% holdout** as its eval split. That measures loss, not execution
accuracy, so `test_model`'s gate always fails and `publish` always refuses.
That is correct behaviour, not a bug — but it is why the downloads below matter
if you want a defensible number in the thesis.

---

## Where things go

The layout is not a convention — it is what the loader functions actually read.
Match it exactly.

```
ML/data/raw/
├── spider/
│   ├── tables.json           <- load_spider(), _load_real_spider_dev()
│   ├── train_spider.json     <- load_spider()
│   ├── train_others.json     <- load_spider()
│   ├── dev.json              <- _load_real_spider_dev()
│   └── database/
│       └── <db_id>/<db_id>.sqlite      <- evaluate_dataset()
│
├── bird/
│   ├── train/
│   │   └── train.json        <- load_bird()  (exactly this nesting)
│   └── dev/
│       ├── dev.json
│       └── dev_databases/
│           └── <db_id>/<db_id>.sqlite
│
└── nvbench/
    ├── nvBench.json          <- model_c/prepare_data.py --nvbench-json
    └── databases/
        └── <db_id>/<db_id>.sqlite      <- --database-root
```

The `<db_id>/<db_id>.sqlite` nesting is required: `evaluate.py` builds the path
as `Path(db_dir) / db_id / f"{db_id}.sqlite"`. A flat folder of `.sqlite` files
will not work.

BIRD's archive folder names have changed between releases (`dev_20240627/` and
similar). Don't trust any instruction about the archive's internals — unzip it,
look, and rearrange to match the tree above.

---

## 1. Spider

Source: <https://yale-lily.github.io/spider> — the download is a Google Drive
link, which `wget` usually cannot follow. **Download it in a browser.**

Mirror if Drive is being difficult: the `xlangai/spider` dataset on HuggingFace
carries the same files.

Unzip so that `ML/data/raw/spider/tables.json` exists, then verify:

```bash
cd ML/data/raw/spider
ls tables.json train_spider.json train_others.json dev.json
ls database | head -3
ls database/concert_singer/concert_singer.sqlite
```

All five must resolve. If `database/` is one flat directory of `.sqlite` files,
re-extract — you have the wrong archive.

## 2. BIRD

Source: <https://bird-bench.github.io/> — **registration required**, which is
the entire reason this is manual. The train and dev packs are separate
downloads.

The train pack usually contains a *nested* `train_databases.zip` that must be
unzipped again.

Arrange to match the tree above, then verify:

```bash
cd ML/data/raw/bird
ls train/train.json
ls dev/dev.json
ls dev/dev_databases | head -3
```

`load_bird()` takes each database's schema from its own `.sqlite` file
(`train/train_databases/<db_id>/<db_id>.sqlite`) — BIRD's `train.json` carries
no DDL — and `load_bird_dev()` does the same for `dev/dev.json` +
`dev/dev_databases/`. A question whose database is missing is skipped with a
warning naming the databases, never trained on without a schema. Check the
count it prints against what you expect (about 9.4k train, 1.5k dev).

## 3. nvBench (Module C)

Source: <https://github.com/TsinghuaDatabaseGroup/nvBench> — clone or download
the repo zip. You need `nvBench.json` and the database folder.

## 4. Base model — you do **not** need this

`Qwen/Qwen2.5-Coder-7B-Instruct` (~15 GB) is pulled by the SageMaker training
container at job start. Downloading it locally buys nothing unless you intend
to train on your own GPU, which this project does not do.

---

## Using what you downloaded

Re-run the pipeline pointing at the real data instead of the HF proxy, then
re-upload:

```bash
export PATH="$PWD/ML/.venv/Scripts:$PATH"      # see ML/README.md §0
cd ML
python -m src.model_a.pipeline.run_pipeline --stage-to split \
    --use-spider-hf false \
    --spider-dir data/raw/spider \
    --bird-dir   data/raw/bird

aws s3 sync data/processed/model_a s3://<datasets-bucket>/model_a/ \
    --profile alibi --no-verify-ssl
```

What changes: `clean` now reads real Spider (and BIRD) instead of the HF proxy,
and `split` uses the real Spider-dev set instead of a seeded holdout — the log
line `eval source: spider_dev` instead of `seeded_holdout` is the confirmation.

Raw datasets can also be mirrored to S3 for the record, though nothing reads
them from there:

| Dataset | S3 path |
|---|---|
| Spider | `s3://<datasets-bucket>/model_a/raw/spider/` |
| BIRD | `s3://<datasets-bucket>/model_a/raw/bird/` |
| nvBench | `s3://<datasets-bucket>/model_c/raw/nvbench/` |

---

## `test_model` and these eval files

Downloading Spider-dev is necessary for a real EX number but **not sufficient**.
`pipeline/test_model.py` does `json.loads(path.read_text())` and hands the result
to `evaluate_dataset`, which reads `sample["schema"]`, `sample["question"]`,
`sample["sql"]` and `sample["db_id"]`. Neither available file fits:

- **Raw `dev.json`** has no `schema` key at all, and its `sql` field is a
  *parsed dict*, not a SQL string (the string lives in `query`). Passing it
  raises `KeyError: 'schema'`.
- **The pipeline's own `data/processed/model_a/eval/eval.jsonl`** has exactly the
  right fields — but it is JSONL, one object per line, and `json.loads` on the
  whole file fails.

So the invocation in `ML/README.md` §5 does not work as written. The
one-line bridge, once Spider-dev is in place:

```bash
python -c "import json,pathlib; p=pathlib.Path('data/processed/model_a/eval/eval.jsonl'); \
json.dump([json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l], \
open('data/processed/model_a/eval/eval.json','w'))"
```

Then pass `--spider-eval-path data/processed/model_a/eval/eval.json` and
`--spider-db-dir data/raw/spider/database`.
