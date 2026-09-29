"""Network canary: a Harbor task that proves the egress policy is enforced.

The canary uses the same two-phase policy as benchmark tasks: an
`[environment]` baseline for agent setup (model API hosts plus any install
hosts) and an `[agent]` allowlist for `agent.run()` (model API hosts only).

- The setup probe runs as the environment healthcheck, inside the agent
  container after Harbor has applied the baseline. It records whether
  blocked URLs are unreachable and whether each model API host connects.
- With `agent_probe=True` (Harbor's `oracle` agent, `tdb env check
  --canary`), `solution/solve.sh` repeats the probe during the agent phase
  and also requires the install hosts to be blocked there.

The verifier turns the probe results into reward 1 (policy holds) or 0.
`tdb report` refuses to report a job whose canary did not score 1.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from pathlib import Path

from trialdesignbench.build import (
    AGENT_HOSTS_MARKER,
    ENVIRONMENT_HOSTS_MARKER,
    TASK_ORG,
    _toml_table,
)

CANARY_TASK_ID = "network-canary"
CANARY_TASK_NAME = f"{TASK_ORG}/{CANARY_TASK_ID}"
BLOCKED_URLS = ("https://clinicaltrials.gov", "https://pubmed.ncbi.nlm.nih.gov")
RESULT_PATH = "/logs/artifacts/canary.json"
AGENT_RESULT_PATH = "/logs/artifacts/canary-agent.json"

_PROBE = r"""#!/usr/bin/env bash
# Network canary probe. Always exits 0; the verifier judges the result file.
set -u
out="{result_path}"
mkdir -p "$(dirname "$out")"
probe() {{
  curl -sS -o /dev/null -m 15 --connect-timeout 10 "$1" 2>/dev/null
  echo $?
}}
{{
  echo '{{"blocked": {{'
  first=1
  for url in {blocked}; do
    rc=$(probe "$url")
    [ $first -eq 1 ] || echo ','
    first=0
    printf '"%s": %s' "$url" "$rc"
  done
  echo '}}, "allowed": {{'
  first=1
  for host in {allowed}; do
    rc=$(probe "https://$host")
    [ $first -eq 1 ] || echo ','
    first=0
    printf '"%s": %s' "$host" "$rc"
  done
  echo '}}}}'
}} > "$out"
cat "$out"
exit 0
"""

_VERIFY = """#!/usr/bin/env bash
# Reward 1 only if, in every probed phase, each blocked URL failed and each
# allowed host connected.
set -uo pipefail
mkdir -p /logs/verifier
python3 - <<'PY'
import json, pathlib
out = pathlib.Path("/logs/verifier")
phases = json.loads({phases!r})
reasons = []
probes = {{}}
for phase, path in phases.items():
    path = pathlib.Path(path)
    if not path.is_file():
        reasons.append(f"{{phase}} probe result missing")
        continue
    data = json.loads(path.read_text())
    probes[phase] = data
    for url, rc in data.get("blocked", {{}}).items():
        if rc == 0:
            reasons.append(f"{{phase}}: blocked URL reachable: {{url}}")
    for host, rc in data.get("allowed", {{}}).items():
        if rc != 0:
            reasons.append(
                f"{{phase}}: allowed host unreachable: {{host}} (curl exit {{rc}})"
            )
    if not data.get("blocked"):
        reasons.append(f"{{phase}}: no blocked URLs probed")
reward = 0.0 if reasons else 1.0
(out / "reward.json").write_text(json.dumps({{"reward": reward}}))
(out / "canary.json").write_text(
    json.dumps({{"probe": probes, "reasons": reasons}}, indent=2)
)
print(json.dumps({{"reward": reward, "reasons": reasons}}))
PY
"""


def _probe_script(
    result_path: str, blocked: Sequence[str], allowed: Sequence[str]
) -> str:
    return _PROBE.format(
        result_path=result_path,
        blocked=" ".join(shlex.quote(u) for u in blocked),
        allowed=" ".join(shlex.quote(h) for h in allowed),
    )


def write_canary_task(
    dest: Path,
    *,
    image: str,
    agent_hosts: Sequence[str],
    setup_hosts: Sequence[str] = (),
    agent_probe: bool = False,
) -> Path:
    """Write the canary task into `dest/network-canary` and return its path.

    `agent_hosts` are the model API hosts allowed in both phases;
    `setup_hosts` are added to the setup baseline only. `agent_probe` makes
    `solution/solve.sh` probe the agent phase, which only Harbor's `oracle`
    agent runs; the verifier then requires that result too.
    """
    task = dest / CANARY_TASK_ID
    (task / "environment").mkdir(parents=True, exist_ok=True)
    (task / "tests").mkdir(parents=True, exist_ok=True)
    (task / "solution").mkdir(parents=True, exist_ok=True)
    environment_hosts = sorted({*agent_hosts, *setup_hosts})
    setup_only = sorted(set(setup_hosts) - set(agent_hosts))
    probe = _probe_script(RESULT_PATH, BLOCKED_URLS, agent_hosts)
    # The setup probe is embedded in the healthcheck so it runs under the
    # baseline before the agent starts; nothing is uploaded into /app.
    healthcheck = f"bash -c {shlex.quote(probe)}"
    toml = "\n\n".join(
        [
            'schema_version = "1.4"',  # /logs/artifacts is collected implicitly
            _toml_table(
                "task",
                {
                    "name": CANARY_TASK_NAME,
                    "version": "1.0.0",
                    "description": (
                        "Asserts the closed-book network policy is enforced."
                    ),
                    "keywords": ["canary"],
                },
            ),
            _toml_table("metadata", {"canary": True}),
            _toml_table(
                "agent",
                {
                    "timeout_sec": 300.0,
                    "user": "agent",
                    "network_mode": "allowlist",
                    "allowed_hosts": list(agent_hosts),
                },
                comments={"allowed_hosts": AGENT_HOSTS_MARKER},
            ),
            _toml_table(
                "environment",
                {
                    "network_mode": "allowlist",
                    "allowed_hosts": environment_hosts,
                    "docker_image": image,
                },
                comments={"allowed_hosts": ENVIRONMENT_HOSTS_MARKER},
            ),
            _toml_table(
                "environment.healthcheck",
                {"command": healthcheck, "timeout_sec": 120.0, "retries": 1},
            ),
            _toml_table(
                "verifier", {"environment_mode": "separate", "timeout_sec": 120.0}
            ),
            _toml_table("verifier.environment", {"network_mode": "no-network"}),
        ]
    )
    (task / "task.toml").write_text(toml + "\n", newline="\n")
    (task / "instruction.md").write_text(
        "This is a network canary task. Do not run any commands or edit any "
        "files. Reply with the single word OK.\n",
        newline="\n",
    )
    phases = {"setup": RESULT_PATH}
    if agent_probe:
        blocked = [*BLOCKED_URLS, *(f"https://{h}" for h in setup_only)]
        solve = _probe_script(AGENT_RESULT_PATH, blocked, agent_hosts)
        phases["agent"] = AGENT_RESULT_PATH
    else:
        solve = "#!/usr/bin/env bash\necho OK\n"
    (task / "solution" / "solve.sh").write_text(solve, newline="\n")
    (task / "solution" / "solve.sh").chmod(0o755)
    test_sh = task / "tests" / "test.sh"
    test_sh.write_text(_VERIFY.format(phases=json.dumps(phases)), newline="\n")
    test_sh.chmod(0o755)
    (task / "tests" / "Dockerfile").write_text(
        f"FROM {image}\n\nCOPY --chmod=755 test.sh /tests/test.sh\n", newline="\n"
    )
    return task


def canary_result(trial_dir: Path) -> tuple[bool, list[str]]:
    """Read a canary trial's verifier output: (passed, reasons)."""
    path = trial_dir / "verifier" / "canary.json"
    if not path.is_file():
        return False, [f"no canary verdict at {path}"]
    data = json.loads(path.read_text())
    reasons = [str(r) for r in data.get("reasons", [])]
    return not reasons, reasons
