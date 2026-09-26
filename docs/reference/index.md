# API reference

The core modules are importable without the CLI dependencies and never import
Harbor.

| Module | Purpose |
| --- | --- |
| [`schema`](schema.md) | Versioned pydantic models for every artifact |
| [`dataset`](dataset.md) | Intake import, canonical dataset, validation |
| [`build`](build.md) | Canonical dataset to Harbor task directories |
| [`environment`](environment.md) | Shared image pins, Dockerfile context, build/check commands |
| [`judge`](judge.md) | Judge protocol, `AnthropicJudge`, `FakeJudge` |
| [`grade`](grade.md) | Deterministic checks, rubric judging, grade outputs |
| [`scoring`](scoring.md) | Versioned scoring rules |
| [`run`](run.md) | `job.yaml` generation, auth, network host table |
| [`canary`](canary.md) | Network canary task |
| [`report`](report.md) | Aggregation and leaderboards |
| [`provenance`](provenance.md) | Digests, versions, git and image identifiers |

::: trialdesignbench
