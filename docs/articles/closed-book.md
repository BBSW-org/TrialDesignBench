# Closed book

This page records why closed book is enforced the way it is, what each layer
can and cannot stop, and what would have to change to relax the rules. It is
a reference for future decisions (rather than a usage guide).
The policy itself is described in [Environment](environment.md#network-policy-closed-book)
and the per-agent settings in [Agents](agents.md#closed-book-settings).

The findings come from reading Harbor 0.23.0's source and from live probe
runs on 2026-09-27 with Claude Code 2.1.283, Codex CLI 0.157.1, and the
Antigravity SDK 0.1.9 (the version Harbor's runner pins). Rerun the
[probe](#probing-an-agent) before relying on them for a newer agent version.

## Summary

- Harbor's `allowlist` network mode is a generic, agent-independent egress control.\
  In the probes it blocked every client-side channel of every agent:
  shell commands (`curl`, Python), the CLIs' own URL fetch tools, and
  third-party search services. Nothing at the Docker image level can add to
  it, because the task container has no network capabilities and Harbor
  already owns its network namespace.
- The one channel it cannot touch is a web tool that runs on the model
  provider's servers and is reached through the model API host that must
  stay open. Claude Code's `WebSearch`, Codex's `web_search` (search and page
  opening), and the Antigravity SDK's `search_web` all behaved this way.
- The per-agent part of closed book therefore cannot be removed, but it
  reduces to one question per agent: which web tools run provider-side, and
  which switch removes them from the model's tool list. `tdb run` supports
  an agent only when such a switch exists (the tool use is **prevented**)
  and refuses it otherwise. The grader's trajectory scan stays a backstop,
  not a substitute.

## How Harbor enforces the allowlist

For Docker and Podman, Harbor starts an egress-control sidecar
(a `gogost/gost` transparent proxy) and runs every task service in the
sidecar's network namespace (`network_mode: service:<sidecar>`).
Only the sidecar gets `NET_ADMIN` and `NET_RAW`.

- nftables rules in the shared namespace redirect all outbound TCP to the
  proxy and reject every other protocol, except DNS to the resolver in
  `/etc/resolv.conf` and ICMP.
- The proxy sniffs the TLS SNI or HTTP `Host` of each connection and lets it
  through only if the name matches the allowlist file. When no name can be
  sniffed, the original destination address is matched instead, which never
  equals a hostname entry. A denied connection is simply closed, so the
  agent sees `curl: (35) ... SSL_ERROR_SYSCALL`, Python's
  `TLS/SSL connection has been closed (EOF)`, or a tool error such as
  `Socket is closed`.
- Phase switches (the `[environment]` baseline during setup, `[agent]` during
  `agent.run()`, the verifier policies) rewrite the allowlist file and the
  rules inside the sidecar with `network-policy allow <hosts>`, `deny-all`
  (for `no-network`), or `allow-all` (for `public`).
- The engine host's kernel needs `nft_fib` support (`CONFIG_NFT_FIB_INET`).
  Harbor probes for it and rejects the trial otherwise (see
  [Local runtime](environment.md#local-runtime)).

Consequences:

- A firewall or proxy baked into the image would be redundant and weaker:
  the image cannot grant itself capabilities, and proxy environment variables
  such as `HTTPS_PROXY` are advisory and ignored by some runtimes.
- The agent cannot alter the rules even as root in the task container, and
  tasks run it as the unprivileged `agent` user anyway.
- DNS resolution works (`getent hosts clinicaltrials.gov` succeeds), so a DNS
  tunnel is theoretically possible. No agent does this on its own, and
  training data is a far larger leak; it is an accepted residual.

## Where each agent's web tools run

"Egress alone" is what happened in a probe with only the model API host
allowlisted and none of `tdb run`'s closed-book kwargs. Rows marked "not
probed" rely on vendor documentation and Harbor's adapter source.

| Agent | Tool | Runs | Egress alone | Switch `tdb run` uses |
| --- | --- | --- | --- | --- |
| `claude-code` | `WebSearch` | Anthropic's web search backend, inside the Messages API call | **Not blocked**: returned clinicaltrials.gov results | `disallowed_tools` plus `permissions.deny` |
| `claude-code` | `WebFetch` | In the CLI: a hostname preflight to `api.anthropic.com`, a local fetch, then a small-model extraction call | Blocked (`Socket is closed`) | same |
| `codex` | `web_search` | OpenAI's hosted web search tool, with `search` and `open_page` actions; defaults to `live` under Harbor's bypass-sandbox flag | **Not blocked**: search results and the `<title>` of example.com both came back | `web_search=disabled` |
| `grok-build` | `web_search` | xAI server-side (docs, not probed) | Presumed not blocked | `disable_web_search=true`, pinned in `/etc/grok/requirements.toml` |
| `grok-build` | `web_fetch` | In the CLI (docs, not probed) | Presumed blocked | `web_fetch=false`, same file |
| `opencode` | `websearch` | Exa's or Parallel's hosted MCP service on their own hosts; off unless `OPENCODE_ENABLE_EXA` or `OPENCODE_ENABLE_PARALLEL` is set (docs, not probed) | Blocked by the allowlist (other hosts) | `permission` deny, belt and braces |
| `opencode` | `webfetch` | In the CLI (docs, not probed) | Blocked | `permission` deny, belt and braces |
| `antigravity-sdk` | `search_web` | A Gemini API server call (`CORTEX_STEP_TYPE_SEARCH_WEB: GenerateContent`) | **Not blocked**: reached Google and failed only on the key's grounding quota | none through Harbor; refused |
| `antigravity-sdk` | `read_url_content` | A local fetch by the SDK's harness binary | Blocked (`Get "https://example.com/": EOF`) | refused |
| `antigravity-cli` | web search | The same Google backend (not probed) | Presumed not blocked | no settings key; refused |
| `kimi-code` | `SearchWeb`, `FetchURL` | Moonshot search and fetch services at configurable base URLs (docs, not probed) | Unknown; they may share the model API host | no ATIF trajectory; refused |
| `muse-code` | `web_search`, `web_fetch` | Meta's service (not probed) | Unknown | `muse exec --disable-web-tools` exists but Harbor does not expose it; no ATIF, no version pin; refused |

Two more observations from the probes:

- The grader's trajectory scan flagged every web tool call and network shell
  command of the Claude Code and Codex runs. Its tool-name list is tuned to
  the supported agents: `search_web`, `read_url_content`, `SearchWeb`, and
  `FetchURL` are not in it and would be caught only when a URL appears in
  the arguments.
- Harbor's Antigravity SDK runner did not record the failed `search_web` and
  `read_url_content` steps as tool calls in the ATIF trajectory. Detection
  cannot be assumed for an agent until a probe shows its web tool calls in
  `trajectory.json`.

## Provider-side controls

- Anthropic: an organization administrator can disable web search in the
  Claude Console. A Messages API request that still includes the tool then
  fails with a 400 `invalid_request_error`. Enabling this on the organization
  that holds the benchmark key is good defense in depth, but `tdb` cannot
  verify it, so the harness switch stays.
- OpenAI: no account-level switch for the hosted web search tool was found;
  `web_search=disabled` in Codex is the control. Codex's own network proxy
  does not filter hosted tools either.
- Gemini API: no account-level switch for Google Search grounding or URL
  context was found. The SDK's `CapabilitiesConfig(disabled_tools=...)` is
  the control and works: with `SEARCH_WEB` and `READ_URL_CONTENT` disabled,
  the model's tool list no longer contains them (verified directly against
  the SDK, outside Harbor).

## Alternatives considered

| Option | Verdict | Why |
| --- | --- | --- |
| Firewall or proxy baked into the image | Rejected | Redundant with Harbor's sidecar and weaker: no capabilities in the task container. |
| TLS-terminating proxy that strips `web_search`-type tools from API request bodies | Rejected | The only fully agent-independent way to stop provider-side tools, but it needs a trusted CA in every CLI runtime, per-provider request rewriting that tracks API schema changes, and it inspects prompts and API keys in flight. |
| Provider account toggles as the only control | Rejected | Only Anthropic offers one, and `tdb` cannot verify it. Recommended as defense in depth. |
| Detection-only tier: run agents without a switch and let the grader zero any trial that used a web tool | Rejected for now | Keeps result integrity but wastes runs and depends on ATIF coverage that is not guaranteed. Prerequisites are listed below in case it is revisited. |
| Upstream changes to Harbor: a `disabled_tools` kwarg for `antigravity-sdk`, ATIF for `kimi-code` and `muse-code` | Not pursued | Small changes, but merge timing is outside our control. |

## Relaxing the policy

If an agent without a harness switch must run some day, do these in order:

1. Probe it (next section) and read where each web tool runs.
2. Add every provider-side tool name to the grader's web tool list
   (`WEB_TOOL_NAMES` in `grade.py`) and add a test that the probe's
   trajectory contains those calls.
3. Record the enforcement level per agent in `tdb-run.json` (for example
   `prevented` or `detected`) so reports can separate the two.
4. Only then add the agent, with the reason it is `detected` rather than
   `prevented` in its profile note.

For the currently refused agents the specific blockers are:

- `antigravity-sdk`: Harbor's runner sets no `CapabilitiesConfig`; a
  `disabled_tools` kwarg would make it `prevented`. Setup needs PyPI for the
  runner's `uv run --script` dependencies (Harbor resolves them during setup
  when it runs the version command; the run phase then works from the
  cache, as the probe showed).
- `antigravity-cli`: no documented settings key disables tools, and Harbor
  rewrites `settings.json` on every run.
- `kimi-code` and `muse-code`: Harbor writes no ATIF trajectory, so nothing
  can be verified; `muse-code` also cannot be version pinned.

## Probing an agent

The probe is an ordinary Harbor task whose `[environment]` baseline is
`public` (so Harbor can install the agent) and whose `[agent]` allowlist
holds only the model API host. It uses a throwaway image rather than the
benchmark image, so it needs no R build. Its instruction asks the agent to
use its web search tool, its URL fetch tool, `curl`, Python `urllib`, and
`getent hosts`, to continue past errors, and to record every outcome
verbatim.

`task.toml`:

```toml
schema_version = "1.4"

[task]
name = "tdb-exp/probe-claude"
version = "1.0.0"
description = "Which web tools still work under egress control alone."

[agent]
timeout_sec = 900.0
network_mode = "allowlist"
allowed_hosts = ["api.anthropic.com"]  # the agent's model API host only

[environment]
network_mode = "public"                # setup may install the agent
docker_image = "tdb-probe:latest"

[verifier]
environment_mode = "separate"
timeout_sec = 120.0

[verifier.environment]
network_mode = "no-network"
```

`tests/test.sh` only writes `{"reward": 1}` to `/logs/verifier/reward.json`
and `tests/Dockerfile` is `FROM tdb-probe:latest`; the evidence is the
trajectory. A minimal image with Node (for npm-installed CLIs), Python and
`uv` (for the Antigravity runner), and `curl`:

```dockerfile
FROM node:24-bookworm-slim
RUN apt-get update \
	&& apt-get install \
		-y \
		--no-install-recommends \
		bash \
		ca-certificates \
		curl \
		git \
		jq \
		procps \
		python3 \
		python3-venv \
	&& rm \
		-rf \
		/var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /usr/local/bin/
RUN mkdir \
	-p \
	/app \
	/logs/agent \
	/logs/verifier \
	/logs/artifacts
WORKDIR /app
```

Run it with the agent's Harbor name and a `provider/model`, without any of
`tdb run`'s closed-book kwargs, then list the tool calls and their results:

```bash
harbor run \
	--path probe-claude \
	--env docker \
	--agent claude-code \
	--model anthropic/claude-sonnet-5 \
	--jobs-dir jobs \
	-y
python3 - <<'PY'
import glob, json
t = json.load(open(glob.glob("jobs/*/probe-claude__*/agent/trajectory.json")[0]))
for s in t["steps"]:
    for c in s.get("tool_calls") or []:
        print(c["function_name"], json.dumps(c.get("arguments"))[:120])
    for r in (s.get("observation") or {}).get("results") or []:
        print("   ->", str(r.get("content"))[:200].replace("\n", " | "))
PY
```

Reading the result:

- A web tool whose result arrives while `curl` fails runs provider-side and
  needs a switch in the harness.
- A tool that fails with a closed connection runs in the container and is
  already covered by the allowlist.
- A tool call the agent reported using but that is missing from the
  trajectory means the grader cannot verify that agent either.

Free-tier Gemini keys allow only a few requests per model per day, which
makes an agentic probe fail on quota; use a billed key.
