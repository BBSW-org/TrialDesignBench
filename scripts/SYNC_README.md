# Sync Dataset

`sync_dataset.py` pulls the latest rows from the master Google Sheet, downloads the protocol/SAP PDFs for any new rows, and pushes everything to the Hugging Face dataset at [`trialdesignbench/source`](https://huggingface.co/datasets/trialdesignbench/source).

## What it does

1. Downloads the Google Sheet as CSV, converts to Parquet → overwrites `data/tdr.parquet`.
2. Diffs against the previous `tdr.parquet` by the `#` column to find new rows.
3. For each new row, downloads:
   - `Study Protocol Link` → `documents/<#>/protocol.pdf`
   - `Protocol+SAP / SAP Link` → `documents/<#>/sap.pdf`
   - Skips empty links. Skips files already on disk. Writes a `*.error.txt` on failure.
4. Uploads the updated `tdr.parquet` and any new PDFs to Hugging Face.

## One-time setup

```bash
# 1. Python deps
pip install -U "huggingface_hub[cli]" pandas pyarrow openpyxl
# pandas + pyarrow are required for the Parquet read/write.
# openpyxl is only needed if the source file is an uploaded .xlsx
# (htmlview URL) rather than a native Google Sheet.

# 2. Authenticate to Hugging Face (write token from https://huggingface.co/settings/tokens)
hf auth login

# 3. Make sure the Google Sheet is shared as "Anyone with the link: Viewer"
#    Sheet URL: https://docs.google.com/spreadsheets/d/1Vb6U9Jzigtg5hLcn4R_5REW_G84cNVk6
```

## Usage

The script reads/writes `data/tdr.parquet` and `documents/` relative to a
**data directory**. Pass `--data-dir` to point it at your local copy; if
omitted, it defaults to the directory containing the script.

```bash
# Preview new rows without changing anything
python3 sync_dataset.py --data-dir /path/to/source --dry-run

# Download new PDFs and update tdr.parquet locally, but don't upload
python3 sync_dataset.py --data-dir /path/to/source --no-upload

# Full sync: fetch sheet, download new PDFs, upload to Hugging Face
python3 sync_dataset.py --data-dir /path/to/source
```

If you keep the script next to your data dir, you can drop `--data-dir`.

## Output layout

```
source/
├── data/
│   └── tdr.parquet               # latest sheet content (snappy-compressed)
├── documents/
│   └── <row_#>/
│       ├── protocol.pdf
│       ├── sap.pdf
│       ├── protocol.error.txt    # only present on download failure
│       └── sap.error.txt
└── sync_dataset.py
```

## Configuration

Edit constants at the top of `sync_dataset.py` if any of these change:

| Variable     | Default                                            | Meaning                                  |
| ------------ | -------------------------------------------------- | ---------------------------------------- |
| `SHEET_ID`   | `1Vb6U9Jzigtg5hLcn4R_5REW_G84cNVk6`                | Google Sheet document ID                 |
| `SHEET_GID`  | `0`                                                | Sheet tab GID (find it in the sheet URL) |
| `HF_REPO`    | `trialdesignbench/source`                          | Hugging Face dataset repo                |
| `PARQUET_PATH` | `data/tdr.parquet`                               | Local Parquet path                       |
| `DOCS_DIR`   | `documents/`                                       | PDF output root                          |

## Troubleshooting

**"Google Sheet is not publicly shared"** — Open the sheet → Share → General access → "Anyone with the link" → Viewer.

**A PDF row writes `*.error.txt`** — The source server rejected the request (often `HTTP 403` on paywalled publisher links like NEJM). The row stays in `tdr.parquet`; only the PDF is missing. Re-running won't retry automatically — delete the `.error.txt` and the partial file, then re-run, if you want to retry.

**Upload is slow** — Throughput is bounded by your upstream bandwidth. For small incremental syncs (a handful of new files) `hf upload` is used per file. For > 200 new files the script falls back to `hf upload-large-folder` which parallelizes and is resumable.

**Diff missed a row whose `#` was reused** — The script keys on the `#` column. If a row's content changed without `#` changing, it won't be detected as new. Inspect manually in that case.

## Notes

- The script only **adds** new rows. Rows deleted from the sheet are kept in the local Parquet file and on Hugging Face — clean those up by hand if needed.
- `tdr.parquet` is always overwritten with the latest sheet content, so edits in the sheet (typo fixes, etc.) propagate.
- PDFs are never re-downloaded if already present. Force a re-download by deleting the file.
