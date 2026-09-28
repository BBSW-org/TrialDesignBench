"""Print latest agent releases as JSON: python scripts/latest_agent_versions.py."""

import json
from urllib.request import urlopen

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

print(json.dumps(versions, indent=2))
