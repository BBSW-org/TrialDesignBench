# Build

`tdb build` turns canonical dataset tasks into Harbor task directories.

```bash
uv run tdb build tmp/dataset --out tmp/tasks [--image REF] [--task-ids ID ...]
```

## Task layout

```text
<tasks_dir>/
  tdb-build.json          dataset digest, package version, template hash, image, time
  <task_id>/
    task.toml
    instruction.md
    environment/          empty when a prebuilt image is used
    tests/
      Dockerfile          FROM <image> + COPY test.sh rubrics.json
      test.sh             runs `tdb grade`
      rubrics.json        hidden rubrics
```

No `solution/` is generated yet. An oracle solution can be added later so
Harbor's `oracle` agent can sanity check each task.

## instruction.md

The instruction is, in order:

1. the prompt template (the packaged copy of the intake
   `template_system_prompt.txt`, or `--prompt-template FILE`),
2. a sandbox note: offline except the model API, R packages preinstalled,
   `install.packages()` and web access will fail,
3. the question block as JSON (`{"prompt": [...]}` with nulled outputs),
4. the required output paths `/app/output.json` and `/app/output.R`,
5. the source document.

First-party harnesses expose no system prompt slot, so the template is the
instruction preamble by design. The instruction never contains rubric text;
the test suite checks this.

## task.toml

- `[task] name = "trialdesignbench/<task_id>"`, version = dataset version.
- `[metadata]`: trial id, task type, question counts, design elements, dataset
  version and digest, template hash, grader source.
- `[agent] timeout_sec = 3600`, `user = "agent"`, `network_mode =
  "allowlist"`, and `allowed_hosts = []` marked `# tdb:agent-allowed-hosts`:
  the allowlist during `agent.run()`, filled by `tdb run` with the model API
  hosts.
- `[environment]`: `docker_image`, `skills_dir = "/skills"`, cpus and memory,
  `network_mode = "allowlist"`, and `allowed_hosts = []` marked
  `# tdb:environment-allowed-hosts`: the baseline during agent setup, filled
  by `tdb run` with the model API hosts plus any install hosts (see
  [Run](run.md#network-allowlists)).
- `[verifier] environment_mode = "separate"`, `timeout_sec = 1800`.
- `[verifier.env]`: `TDB_JUDGE_MODEL` and
  `ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"`.
- `[verifier.environment]`: `network_mode = "allowlist"` with only the judge
  API host.
- `artifacts = ["/app/output.json", "/app/output.R", "/logs/agent/trajectory.json"]`.

!!! note "Why the verifier image is built from `tests/Dockerfile`"
    For separate verifiers Harbor does not upload `tests/` at runtime; the
    verifier image must already contain `/tests/test.sh`. A prebuilt
    `[verifier.environment] docker_image` would therefore start without the
    rubrics. Instead, `tests/Dockerfile` is `FROM` the same shared image and
    copies `test.sh` and `rubrics.json` in. The base image is recorded as
    `metadata.verifier_base_image`.

## Grader source

`--grader-source` controls how `test.sh` gets the grader:

| Value | Behavior |
| --- | --- |
| `image` (default) | runs the grader preinstalled in the image; fails if its version differs from the task's |
| `pypi` | `uvx --from trialdesignbench[judge]==<version> tdb grade ...`; adds PyPI hosts to the verifier allowlist |
| `editable` | copies this package's source into `tests/grader/` for local development |

## Dockerfile mode

`--dockerfile` copies the environment Dockerfile and its build context into
`environment/` (and into `tests/` for the verifier) instead of setting
`docker_image`, so Harbor builds the image itself.

## Missing documents

`tdb build` refuses tasks without a source document. `--allow-missing-document`
builds them anyway with a visible placeholder and `metadata.document_missing =
true`. Use it for pipeline smoke tests only; such tasks are not valid for
evaluation.
