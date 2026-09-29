# Dataset

Curated reviewer submissions (`data/json/*.json`) are imported into a
canonical, versioned dataset that every later stage reads.

## Import

```bash
uv run tdb dataset import \
	data/json/*.json \
	--out tmp/dataset \
	--documents path/to/documents \
	--dataset-version 1.0.0
```

- **One task per trial.** The task id is a filesystem- and registry-safe slug
  of `trial_id`: `10.1200_jco-24-01818` becomes `10-1200-jco-24-01818`. A
  leading URL scheme and `doi.org/` are dropped, so
  `doi.org/10.1056/nejmoa2505669` becomes `10-1056-nejmoa2505669`. The
  original `trial_id` is kept in `task.json`.
- **Submission selection.** With several submissions for one trial, the latest
  `submittedAt` wins. Pass `--username` to pick one reviewer's submission
  instead. The choice (`latest` or `username`), the number of candidates, and
  the intake file's sha256 are recorded.
- **Documents.** `--documents DIR` looks for `<task_id>.md`, `<trial_id>.md`
  (with `/` replaced by `_`), or `<intake file stem>.md`. A task without a
  document is imported with `document: null`.
- **Exposure.** `--publicly-indexed/--not-publicly-indexed` records whether the
  source documents are publicly indexed (journal supplement, registry), so
  reports can split results by training-data exposure. The default is unknown.

## Layout

```text
<dataset_dir>/
  dataset.json      schema_version, dataset_version, per-task digests, digest
  <task_id>/
    task.json       TaskRecord: trial id, source submission, document ref,
                    agent-visible question skeleton
    document.md     Protocol/SAP text (Mathpix Markdown)
    rubrics.json    Hidden RubricSet: questions + criteria per dimension
```

`rubrics.json` is the only file with rubric text. It is copied into each
Harbor task's `tests/` directory, which runs in a separate verifier container
the agent never sees.

Criteria get stable ids of the form `<question>/<dimension>/<n>`, for example
`P-004/inputs-used/2`. Extraction questions use the dimension slug `answer`.

## Check

```bash
uv run tdb dataset check tmp/dataset
```

The check fails (non-zero exit) when a task has no source document, a document
does not match its recorded sha256, a file was changed after import (digest
mismatch), question ids differ between `task.json` and `rubrics.json`, or a
question has no `Add` criteria.

## Attach a document later

```bash
uv run tdb dataset attach-document \
	tmp/dataset \
	10-1200-jco-24-01818 \
	sap.md \
	--source "Mathpix conversion of the JCO protocol supplement"
```

This writes `document.md`, updates `task.json`, and refreshes every digest.

## Rubric scoring values

Only `scoring: Add` criteria are scored. Any other value (for example
`Deduct`) is imported and recorded, excluded from the score, and reported as a
warning in `grade.json`. Support for other scoring kinds needs a new scoring
version.
