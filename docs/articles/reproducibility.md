# Reproducibility

Every artifact records what produced it.

| Artifact | Records |
| --- | --- |
| `dataset.json` | schema version, dataset version, package version, per-task content digests, dataset digest |
| `task.json` | source submission id, reviewer, `submittedAt`, intake file sha256, selection rule, document sha256 |
| `tdb-build.json` | dataset digest, package version, prompt template sha256, image ref, image pins, grader source, time |
| `task.toml` `[metadata]` | dataset version and digest, task digest, template sha256, verifier base image |
| `tdb-run.json` | package and Harbor versions, dataset and tasks digests, image ref and digest, agents, models, agent versions, auth mode, skills, judge model, effective network policy (agent-phase and setup allowlists, per-agent setup hosts, disabled tools, canary), git SHA of this repository, command, timestamps, exit code |
| `grade.json` | scoring version, package version, judge model, judge prompt sha256, SDK version, votes, rubrics sha256, submission file hashes |
| `report.json` | package and scoring versions, threshold, sources |

Harbor adds its own `lock.json` per job and trial (task digests, skill
digests and git commits).

## Pins

- Python dependencies are locked in `uv.lock`. `[tool.uv] exclude-newer =
  "7 days"` keeps resolution away from packages published in the last week.
- The environment image pins R, the CRAN snapshot date, Node, `uv`, the agent
  CLIs, and the skills commit (see [Environment](environment.md)); the
  grader inside the image is installed with dependencies exported from
  `uv.lock`. Agents that Harbor installs during setup (grok-build, opencode)
  get the pinned version as Harbor's agent `version`, and the image records
  every agent pin as a label that `tdb run` checks.
- Scoring rules carry `scoring_version`; changing weights or aggregation
  requires a new version.
- The judge prompt and response schema are hashed into every `grade.json`.

## Re-scoring

Because grading is decoupled from inference, rubric or grader changes do not
require new agent runs: `tdb regrade` re-scores recorded trials through
`harbor job regrade`, and `tdb grade` can re-score any saved submission
directory.
