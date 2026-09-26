# Environment

One shared, pinned Docker image serves both the agent and the verifier.

## Contents

| Component | Pin |
| --- | --- |
| Base | `rocker/r-ver:4.5.2` (multi-arch: amd64, arm64) |
| CRAN packages | Posit Package Manager snapshot `2026-09-25` |
| R packages | gsDesign, gsDesign2, rpact, lrstat, graphicalMCP, eventPred, survival, mvtnorm, jsonlite, dplyr, ggplot2, digest |
| Python | 3.12 (Ubuntu noble) with `uv` 0.12.17 |
| Node | 24.20.0 LTS |
| Agent CLIs | Claude Code 2.1.277, Codex CLI 0.155.1 |
| Skills | `RConsortium/pharma-skills` at a pinned commit, in `/skills/<name>/SKILL.md` |
| Grader | `trialdesignbench[judge]` at the package version, in `/opt/tdb/venv` |

The pins live in `trialdesignbench.environment.PINS`. `tdb env build` passes
them as build arguments and records them as image labels
(`org.trialdesignbench.*`). The test suite fails if the Dockerfile's `ARG`
defaults drift from the pins.

The image has a non-root `agent` user that owns `/app` and `/logs`. Tasks run
the agent as that user (`[agent] user = "agent"`), so it cannot edit
`/etc/resolv.conf` or interfere with Harbor's egress sidecar.

## Build and check

```bash
uv run tdb env build                  # native platform, tag trialdesignbench-env:<version>
uv run tdb env build --platform linux/amd64 --platform linux/arm64 --push --tag ghcr.io/org/tdb-env:1.0.0
uv run tdb env check                  # tools, R packages, skills, versions
uv run tdb env check --canary         # plus the network canary (needs harbor)
```

`--tdb-source local` (the default in a source checkout) builds a wheel of the
current package and installs it with dependencies pinned from `uv.lock`;
`--tdb-source pypi` installs the released version instead.

## Network policy (closed book)

Closed book is enforced, not requested. There are three leakage channels.

### 1. Container egress

Every task uses `network_mode = "allowlist"`. `tdb build` writes an empty agent
allowlist (deny all); `tdb run` fills in the model API hosts for the agent and
auth mode from a versioned table (`trialdesignbench.run.AGENT_HOSTS`):

| Agent | Auth | Hosts | Status |
| --- | --- | --- | --- |
| claude-code | api | `api.anthropic.com` | verified by canary and a smoke run |
| claude-code | subscription | `api.anthropic.com` | unverified: add any OAuth refresh host a smoke run shows |
| codex | api | `api.openai.com` | verified by table only |
| codex | subscription | `chatgpt.com`, `auth.openai.com` | unverified |

Hostnames are exact (no wildcards) to keep the policy portable. There are no
agent-phase overrides, and `tdb run` never passes `--allow-agent-host` or
`--allow-environment-host`. Agent CLIs are preinstalled at pinned versions, so
Harbor skips its `npm install` and trials need no registry access; `tdb run`
refuses an `--agent-version` that differs from the image.

The verifier container uses its own allowlist with only the judge API host
(`api.anthropic.com`), and the grader is preinstalled so it needs no PyPI.

### 2. Server-side web tools

Provider-side web tools never touch the container network, so they are
disabled in the harness:

- Claude Code: `disallowed_tools=WebSearch,WebFetch` plus a native settings
  file with `permissions.deny` for the same tools.
- Codex: `web_search=disabled`.
- Gemini CLI: **unsupported.** Harbor's adapter writes
  `~/.gemini/settings.json` itself and accepts no native config, so web tools
  cannot be excluded. `tdb run` refuses `gemini-cli`.
- No MCP servers are ever added.

The grader then scans the ATIF trajectory for web tool calls, URLs in tool
arguments, and network shell commands (see [Grade](grade.md)).

### 3. Parametric memory

Training data cannot be blocked. The instruction keeps the closed-book rule,
the judge fails any criterion supported by a source outside the document, and
`task.json` records whether the document is publicly indexed so reports can
split results by exposure. A future contamination probe (for example, asking
the agent to name the trial from the document alone) can hook in here.

## Network canary

The canary is a Harbor task whose environment healthcheck runs inside the
agent container after the policy is applied. It asserts that
`https://clinicaltrials.gov` and `https://pubmed.ncbi.nlm.nih.gov` fail and
that each allowed model API host connects. The verifier scores 1 only if all
expectations hold.

- `tdb env check --canary` runs it with Harbor's `oracle` agent.
- `tdb run --canary` runs it as the first task of the job, and `tdb report`
  refuses to report a job whose canary did not pass.

## Local runtime

Linux is the preferred host. Harbor's `allowlist` mode needs a Linux kernel
with `nft_fib` support. Docker Desktop's LinuxKit kernel may lack it, in which
case Harbor rejects the policy. On macOS use OrbStack or a remote Linux Docker
host. `tdb env check` warns when it detects Docker Desktop.
