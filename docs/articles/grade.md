# Grade

```text
uv run tdb grade \
  <submission_dir> \
  --rubrics rubrics.json \
  --out <dir> \
  [--trajectory trajectory.json] \
  [--judge anthropic|opencode-go|fake] \
  [--judge-model ID] \
  [--judge-votes K]
```

The grader is a pure function of the submission artifacts, the rubrics, and
the judge configuration. Inside Harbor, `tests/test.sh` runs it as
`tdb grade /app --rubrics /tests/rubrics.json --out /logs/verifier
--trajectory /logs/agent/trajectory.json`. The same command grades any
directory with `output.json` and `output.R`, such as third-party submissions
or subscription-based manual runs.

## Fail loudly

A grading error never produces a passing or silently partial score. Every
problem is an explicit status in `grade.json`, and any gating failure sets the
reward score to 0 while the raw `rubric_score` is kept.

| `status` | Meaning |
| --- | --- |
| `ok` | all gates passed; `score = rubric_score` |
| `zeroed` | a gate failed (bad `output.json`, `output.R` failed, network violation); `score = 0` |
| `error` | something could not be checked (no R runtime, no trajectory, judge failure); `score = 0` |

`tdb grade` exits 0 after writing its outputs, or 3 when the status is
`error`.

## Deterministic checks

| Check | Blocking | Pass when |
| --- | --- | --- |
| `output_json` | yes | `output.json` parses; it has a single top-level `output` array; ids match the skeleton exactly (none missing, extra, or duplicated); no fields added, removed, or renamed; skeleton values unchanged; every previously null field is filled (a "not derivable" string counts, blank strings do not) |
| `output_r` | yes | `output.R` exists and `Rscript output.R` exits 0 within the timeout (default 600 s). It runs in a scratch copy of the submission. A missing R runtime is `error`, never `pass`. |
| `numeric_format` | no | every `calculated_value` has a number with at least 4 decimals and none with 1 to 3, or says the value is not derivable. Reported only. |
| `network` | yes | the ATIF trajectory has no web tool calls (`WebSearch`, `WebFetch`, `web_search`, `web_fetch`, `web_search_call`, `google_web_search`, matched case-insensitively), no `http(s)://` URL in any tool arguments, and no shell command using `curl`, `wget`, `download.file`, `httr`, `requests`, `urllib`, `pip install`, `install.packages`, or `git clone`. Each hit is listed under `network_violations` with its step index. A missing trajectory is `error`. |

The trajectory is taken from `--trajectory`, else `<submission>/trajectory.json`,
else `/logs/agent/trajectory.json`. External submissions without a trajectory
cannot be verified as closed book and therefore score 0 (their
`rubric_score` is still reported).

The URL rule is deliberately strict: a URL written into a file through a tool
call (for example a comment in `output.R`) also counts. Review
`network_violations` in `grade.json` when a trial is zeroed.

The web tool names cover the supported agents. Before an agent is added, its
provider-side tool names must appear in this list and in a probe trajectory
(see [Closed book](closed-book.md#where-each-agents-web-tools-run)); a call
that Harbor does not record cannot be detected here.

## Rubric judging

Each question's `Add` criteria go to the judge in one call. The judge sees the
question, the agent's `output.json` entry for that question, and for
derivation questions the full `output.R`. It never sees other questions'
rubrics. Each criterion gets `pass`, `fail`, or `unclear` with a rationale and
a quoted evidence excerpt.

The default `AnthropicJudge` uses the Anthropic Messages API with structured
JSON output (`output_config.format`), model `claude-opus-5-5` unless
`--judge-model` or `TDB_JUDGE_MODEL` says otherwise; an `opencode-go/<id>`
model selects `OpencodeGoJudge` instead. Model choice, sampling settings,
credentials, and network access are covered in
[Judge](judge.md). Transient errors (rate limits,
5xx, truncated or malformed responses) are retried with backoff. After the
retries, every criterion of that question is marked `error`. `--judge-votes K`
repeats the call K times and takes the majority; ties are `unclear` and any
error vote is `error`.

The judge prompt enforces the closed-book rule: an answer that relies on a
source outside the provided document fails the criteria it supports.

`FakeJudge` gives deterministic verdicts from a mapping or a rule, for tests.
`--judge fake --fake-verdict pass` exposes it on the CLI for dry runs.

## Scoring (`scoring_version = "1"`)

- Criterion weight by importance: High 3, Medium 2, Low 1.
- Question score = sum(weight of passed criteria) / sum(weight of `Add`
  criteria). `unclear` and `error` earn 0 and are counted.
- Task `rubric_score` = mean of question scores. Questions without `Add`
  criteria are unscorable, excluded, and reported as warnings.
- Breakdowns: per rubric dimension (pooled weights; extraction questions under
  `extraction`), per design element, and per question type.
- Reward `score` = `rubric_score` if every blocking check passed and no
  criterion errored, else 0.

## Outputs

| File | Content |
| --- | --- |
| `grade.json` | full `TaskGrade`: checks, violations, per-criterion verdicts, breakdowns, judge model, prompt hash, SDK version, submission hashes |
| `reward.json` | `{"reward", "score", "rubric", "deterministic"}`; Harbor reads `reward` as the headline |
| `reward-details.json` | per-criterion tree rendered by `harbor view` |
| `rscript-stdout.txt`, `rscript-stderr.txt` | captured `Rscript` output |
| `judge/<question_id>.json` | raw judge requests and responses |
