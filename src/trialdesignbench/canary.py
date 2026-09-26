"""Network canary: a Harbor task that proves the egress policy is enforced.

The probe runs as the environment healthcheck, inside the agent container
after Harbor has applied the task's network policy. It records whether
blocked hosts are unreachable and whether each allowed model API host
connects. The verifier turns the probe result into reward 1 (policy holds)
or 0. `tdb report` refuses to report a job whose canary did not score 1.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from pathlib import Path

from trialdesignbench.build import (
    AGENT_HOSTS_MARKER,
    TASK_ORG,
    _toml_table,
)

CANARY_TASK_ID = "network-canary"
CANARY_TASK_NAME = f"{TASK_ORG}/{CANARY_TASK_ID}"
BLOCKED_URLS = ("https://clinicaltrials.gov", "https://pubmed.ncbi.nlm.nih.gov")
RESULT_PATH = "/logs/artifacts/canary.json"

_PROBE = r"""#!/usr/bin/env bash
# Network canary probe. Always exits 0; the verifier judges the result file.
set -u
out="{result_path}"
mkdir -p "$(dirname "$out")"
probe() {{ curl -sS -o /dev/null -m 15 --connect-timeout 10 "$1" 2>/dev/null; echo $?; }}
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
# Reward 1 only if every blocked URL failed and every allowed host connected.
set -uo pipefail
mkdir -p /logs/verifier
python3 - <<'PY'
import json, pathlib
out = pathlib.Path("/logs/verifier")
path = pathlib.Path("{result_path}")
reasons = []
if not path.is_file():
    reasons.append("canary probe result missing")
    data = {{}}
else:
    data = json.loads(path.read_text())
    for url, rc in data.get("blocked", {{}}).items():
        if rc == 0:
            reasons.append(f"blocked URL reachable: {{url}}")
    for host, rc in data.get("allowed", {{}}).items():
        if rc != 0:
            reasons.append(f"allowed host unreachable: {{host}} (curl exit {{rc}})")
    if not data.get("blocked"):
        reasons.append("no blocked URLs probed")
reward = 0.0 if reasons else 1.0
(out / "reward.json").write_text(json.dumps({{"reward": reward}}))
(out / "canary.json").write_text(json.dumps({{"probe": data, "reasons": reasons}}, indent=2))
print(json.dumps({{"reward": reward, "reasons": reasons}}))
PY
"""


def write_canary_task(
    dest: Path,
    *,
    image: str,
    allowed_hosts: Sequence[str],
) -> Path:
    """Write the canary task into `dest/network-canary` and return its path."""
    task = dest / CANARY_TASK_ID
    (task / "environment").mkdir(parents=True, exist_ok=True)
    (task / "tests").mkdir(parents=True, exist_ok=True)
    (task / "solution").mkdir(parents=True, exist_ok=True)
    probe = _PROBE.format(
        result_path=RESULT_PATH,
        blocked=" ".join(shlex.quote(u) for u in BLOCKED_URLS),
        allowed=" ".join(shlex.quote(h) for h in allowed_hosts),
    )
    # The probe is embedded in the healthcheck so it runs under the baseline
    # policy before the agent starts; nothing is uploaded into /app.
    healthcheck = f"bash -c {shlex.quote(probe)}"
    toml = "\n\n".join(
        [
            'schema_version = "1.4"',  # /logs/artifacts is collected implicitly
            _toml_table(
                "task",
                {
                    "name": CANARY_TASK_NAME,
                    "version": "1.0.0",
                    "description": "Asserts the closed-book network policy is enforced.",
                    "keywords": ["canary"],
                },
            ),
            _toml_table("metadata", {"canary": True}),
            _toml_table("agent", {"timeout_sec": 300.0, "user": "agent"}),
            _toml_table(
                "environment",
                {
                    "network_mode": "allowlist",
                    "allowed_hosts": list(allowed_hosts),
                    "docker_image": image,
                },
                comments={"allowed_hosts": AGENT_HOSTS_MARKER},
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
    (task / "solution" / "solve.sh").write_text(
        "#!/usr/bin/env bash\necho OK\n", newline="\n"
    )
    (task / "solution" / "solve.sh").chmod(0o755)
    test_sh = task / "tests" / "test.sh"
    test_sh.write_text(_VERIFY.format(result_path=RESULT_PATH), newline="\n")
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
