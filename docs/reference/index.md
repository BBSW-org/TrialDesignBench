# API reference

The core modules are importable without the CLI dependencies and never import
Harbor. The one module that does, `harbor_agents`, is a Harbor plugin: Harbor
imports it inside its own process, and no `tdb` command does.

| Module | Purpose |
| --- | --- |
| [`schema`](schema.md) | Versioned pydantic models for every artifact |
| [`dataset`](dataset.md) | Intake import, canonical dataset, validation |
| [`build`](build.md) | Canonical dataset to Harbor task directories |
| [`environment`](environment.md) | Shared image pins, Dockerfile context, build/check commands |
| [`providers`](providers.md) | Model providers shared by agents and judges: name, key variable, API host |
| [`judge`](judge.md) | Judge protocol and one judge per provider: `AnthropicJudge`, `OpenaiJudge`, `XaiJudge`, `OpencodeGoJudge`, plus `FakeJudge` |
| [`grade`](grade.md) | Deterministic checks, rubric judging, grade outputs |
| [`scoring`](scoring.md) | Versioned scoring rules |
| [`agents`](agents.md) | Supported agents: providers, credentials, hosts, closed-book settings |
| [`harbor_agents`](harbor_agents.md) | Harbor plugin: the agent classes `tdb run` launches, with a file-based instruction transport |
| [`run`](run.md) | `job.yaml` generation, allowlists, plugin copy, provenance manifest |
| [`canary`](canary.md) | Network canary task |
| [`report`](report.md) | Aggregation and leaderboards |
| [`provenance`](provenance.md) | Digests, versions, git and image identifiers |

::: trialdesignbench
