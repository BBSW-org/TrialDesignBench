# Report

```bash
uv run tdb report jobs/<job> [more dirs...] [--out report.json] [--format table|json|md] \
  [--threshold 0.8] [--task-ids ...]
```

## Inputs

- **Harbor job directories.** Each trial's `result.json` and
  `verifier/grade.json` are read (falling back to `verifier/reward.json`).
  The job's `config.json` defines the expected tasks.
- **Standalone directories** of `<task_id>/grade.json` produced by `tdb grade`
  with no Harbor involved. The directory name is used as the model label, so
  external submissions sit on the same leaderboard.

## Aggregation

Trials are grouped by agent × model × task.

- Per task: mean, min, and max score over attempts, and "all attempts pass"
  (every attempt scores at least `--threshold`, a pass^k style metric).
- Per agent × model: `mean_score` is the mean over **all expected tasks**;
  tasks never attempted count as 0 and are listed as missing. Trials that
  errored (agent crash, no verifier output, grade status `error`) score 0 and
  are listed.
- Breakdowns by question type, design element, and rubric dimension are the
  mean of the per-trial rubric breakdowns.
- Token and cost totals come from each trial's trajectory `final_metrics`,
  falling back to Harbor's `agent_result`.

Every trial with a non-`graded` status or network violations is listed with
its reasons.

## Canary

If a job contains a network canary trial that did not pass, `tdb report`
refuses to report and exits with an error. Canary trials are never scored.
