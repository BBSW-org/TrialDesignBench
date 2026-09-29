# Environment

One shared, pinned Docker image serves both the agent and the verifier.

## Contents

| Component | Pin |
| --- | --- |
| Base | `rocker/r-ver:4.6.1` (multi-arch: amd64, arm64) |
| CRAN packages | Posit Package Manager snapshot `2026-09-27` |
| R packages | gsDesign, gsDesign2, rpact, lrstat, graphicalMCP, eventPred, survival, mvtnorm, jsonlite, dplyr, ggplot2, digest |
| Python | 3.12 (Ubuntu noble) with `uv` 0.12.20 |
| Node | 24.21.0 LTS |
| Agent CLIs | Claude Code 2.1.284, Codex CLI 0.158.0 (preinstalled) |
| Agent settings | `/etc/grok/requirements.toml` for Grok Build 1.0.41; OpenCode 1.18.33 plugin package and model catalog (both CLIs are installed by Harbor at setup) |
| Skills | `RConsortium/pharma-skills` at a pinned commit, in `/skills/<name>/SKILL.md` |
| Grader | `trialdesignbench[judge]` at the package version, in `/opt/tdb/venv` |

The pins live in `trialdesignbench.environment.PINS`. `tdb env build` passes
them as build arguments and records them as image labels
(`org.trialdesignbench.*`). The test suite fails if the Dockerfile's `ARG`
defaults drift from the pins. The OpenCode model catalog
(`/opt/tdb/opencode-models.json`) is downloaded from `models.opencode.ai`
when the image is built, because the catalog bundled in the CLI lags behind
new models and trials cannot fetch it; the image digest in `tdb-run.json`
identifies it and `tdb env check` prints its hash.

The image has a non-root `agent` user that owns `/app` and `/logs`. Tasks run
the agent as that user (`[agent] user = "agent"`), so it cannot edit
`/etc/resolv.conf` or interfere with Harbor's egress sidecar.

## Build and check

```bash
uv run tdb env build # native platform, tag trialdesignbench-env:<version>
uv run tdb env build \
	--platform linux/amd64 \
	--platform linux/arm64 \
	--push \
	--tag ghcr.io/org/tdb-env:1.0.0
uv run tdb env check # tools, R packages, skills, versions
# plus the network canary (needs harbor)
uv run tdb env check \
	--canary \
	--agent grok-build
```

`--tdb-source local` (the default in a source checkout) builds a wheel of the
current package and installs it with dependencies pinned from `uv.lock`;
`--tdb-source pypi` installs the released version instead.

## Network policy (closed book)

Closed book is enforced, not requested. There are three leakage channels.

### 1. Container egress

Every task uses `network_mode = "allowlist"` in two phases, which Harbor
switches between inside the same container:

| Phase | `task.toml` table | Applies during | Allowed hosts |
| --- | --- | --- | --- |
| Setup | `[environment]` | environment start, healthcheck, agent setup | model API hosts, plus install hosts for agents Harbor installs at setup |
| Agent | `[agent]` | `agent.run()` | model API hosts only |

`tdb build` writes both lists empty (deny all); `tdb run` fills them from the
versioned agent table (`trialdesignbench.agents`, see
[Agents](agents.md#network)). Claude Code and Codex are preinstalled at
pinned versions, so Harbor skips its install, the two lists are equal, and
trials need no registry access. Harbor's grok-build and opencode adapters
reinstall their CLI at every setup regardless of the image, so during setup
only their install hosts are also reachable (Ubuntu mirrors and `x.ai`;
GitHub, `nodejs.org`, and the npm registry). The agent never runs under the
setup policy. `tdb run` refuses an `--agent-version` that differs from the
pin.

Hostnames are exact (no wildcards) to keep the policy portable. `tdb run`
never passes `--allow-agent-host` or `--allow-environment-host`.

Harbor enforces the allowlist with an egress sidecar that shares the task
container's network namespace: nftables redirects all outbound TCP to a
transparent proxy that matches the TLS SNI or HTTP `Host` against the
allowlist and closes everything else; other protocols are rejected except
DNS and ICMP. Only the sidecar has network capabilities, so nothing in the
image could enforce more than this. Probe runs confirmed that it blocks
shell commands and the CLIs' own URL fetch tools for every agent; see
[Closed book](closed-book.md#how-harbor-enforces-the-allowlist).

The verifier container uses its own allowlist with only the judge API host
(`api.anthropic.com`), and the grader is preinstalled so it needs no PyPI.

### 2. Server-side web tools

Web tools that run on the model provider's servers are reached through the
allowlisted model API host, so the allowlist cannot stop them. Probe runs
showed Claude Code's `WebSearch`, Codex's `web_search`, and the Antigravity
SDK's `search_web` returning live results under the agent allowlist (see
[Closed book](closed-book.md#where-each-agents-web-tools-run)). They are
therefore disabled in the harness, and an agent without such a switch is
refused:

- Claude Code: `disallowed_tools=WebSearch,WebFetch` plus a native settings
  file with `permissions.deny` for the same tools.
- Codex: `web_search=disabled`.
- Grok Build: `disable_web_search=true` and `web_fetch` off, in Harbor's
  generated config and pinned in the image's root-owned
  `/etc/grok/requirements.toml`, which user config cannot override.
- OpenCode: `webfetch` and `websearch` denied through `permission`, which
  removes them from the model's tool list.
- Antigravity (`antigravity-sdk`, `antigravity-cli`): **unsupported.**
  Harbor's SDK runner enables every tool and never sets the SDK's
  `disabled_tools`, and its CLI adapter rewrites the settings file on every
  run; neither accepts an option to disable web tools. `search_web` runs
  inside the Gemini API call, so the allowlist does not stop it
  (`read_url_content` is a local fetch and is blocked). `tdb run` refuses
  them.
- No MCP servers are ever added.

The CLIs' local fetch tools (Claude Code `WebFetch`, OpenCode `webfetch`,
Grok Build `web_fetch`) are blocked by the allowlist anyway; disabling them
too saves failed attempts. The grader then scans the ATIF trajectory for
web tool calls, URLs in tool arguments, and network shell commands (see
[Grade](grade.md)).

### 3. Parametric memory

Training data cannot be blocked. The instruction keeps the closed-book rule,
the judge fails any criterion supported by a source outside the document, and
`task.json` records whether the document is publicly indexed so reports can
split results by exposure. A future contamination probe (for example, asking
the agent to name the trial from the document alone) can hook in here.

## Network canary

The canary is a Harbor task with the same two-phase policy. Its environment
healthcheck runs inside the agent container after the setup policy is
applied and asserts that `https://clinicaltrials.gov` and
`https://pubmed.ncbi.nlm.nih.gov` fail and that each model API host
connects. The verifier scores 1 only if all expectations hold.

- `tdb env check --canary --agent <name> [--provider <provider>]` runs it
  with Harbor's `oracle` agent, whose solution repeats the probe during
  `agent.run()` and additionally requires the agent's install hosts to be
  blocked there.
- `tdb run --canary` runs it as the first task of the job (setup phase), and
  `tdb report` refuses to report a job whose canary did not pass.

## Local runtime

Linux is the preferred host. Harbor's `allowlist` mode needs a Linux kernel
with `nft_fib` support. Docker Desktop's LinuxKit kernel may lack it, in which
case Harbor rejects the policy. On macOS use OrbStack or a remote Linux Docker
host. `tdb env check` warns when it detects Docker Desktop.
