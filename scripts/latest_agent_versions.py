"""Print latest agent and uv releases as JSON.

Run with: python scripts/latest_agent_versions.py
"""

import json
from urllib.request import Request, urlopen

versions = {}
for agent, package in {
    "claude-code": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "opencode": "@opencode-ai/sdk",
}.items():
    with urlopen(
        f"https://registry.npmjs.org/{package}/latest", timeout=30
    ) as response:
        versions[agent] = json.load(response)["version"]

# Stable channel URL from https://x.ai/cli/install.sh.
with urlopen("https://x.ai/cli/stable", timeout=30) as response:
    versions["grok-build"] = response.read().decode().strip()

# The host rejects urllib's default User-Agent.
request = Request(
    "https://astral.sh/uv/install.sh", headers={"User-Agent": "trialdesignbench"}
)
with urlopen(request, timeout=30) as response:
    versions["uv"] = response.read().decode().split('APP_VERSION="')[1].split('"')[0]

print(json.dumps(versions, indent=2))
