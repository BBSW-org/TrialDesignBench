# syntax=docker/dockerfile:1.7
#
# TrialDesignBench shared environment: one pinned image for the agent and the
# verifier. Multi-arch (linux/amd64, linux/arm64).
#
# Build with `tdb env build`, which stages this context (including a grader
# wheel) and passes the pins from `trialdesignbench.environment.PINS`. The
# ARG defaults below must equal those pins (enforced by the test suite).

ARG R_VERSION=4.6.1
ARG UV_VERSION=0.12.19

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM rocker/r-ver:${R_VERSION}

ARG R_VERSION=4.6.1
ARG UV_VERSION=0.12.19
ARG CRAN_SNAPSHOT=2026-09-27
ARG UBUNTU_CODENAME=noble
ARG NODE_VERSION=24.21.0
ARG CLAUDE_CODE_VERSION=2.1.283
ARG CODEX_VERSION=0.157.1
ARG GROK_BUILD_VERSION=1.0.40
ARG OPENCODE_VERSION=1.18.32
ARG PHARMA_SKILLS_REPO=https://github.com/RConsortium/pharma-skills.git
ARG PHARMA_SKILLS_COMMIT=4bd5632509a343a674d0762266f1cea9a6d382ad
ARG TDB_VERSION=1.2.2
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    DISABLE_AUTOUPDATER=1 \
    DISABLE_TELEMETRY=1 \
    DISABLE_ERROR_REPORTING=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    UV_NO_CACHE=1

# --- System tools -----------------------------------------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git jq procps ripgrep xz-utils \
        python3 python3-venv \
        libcurl4-openssl-dev libssl-dev libxml2-dev libuv1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

# --- Node LTS (Harbor agent CLIs are npm packages) --------------------------
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
        amd64) node_arch=x64 ;; \
        arm64) node_arch=arm64 ;; \
        *) echo "unsupported arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    base="https://nodejs.org/dist/v${NODE_VERSION}"; \
    tarball="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    curl -fsSLO "${base}/${tarball}"; \
    curl -fsSL "${base}/SHASUMS256.txt" | grep " ${tarball}\$" | sha256sum -c -; \
    tar -xJf "${tarball}" -C /usr/local --strip-components=1 --no-same-owner; \
    rm "${tarball}"; \
    node --version; npm --version

# --- Agent CLIs, pinned. Harbor skips its own install when the version
# --- matches, so trials need no npm registry access. grok-build and
# --- opencode are installed by Harbor during agent setup instead (its
# --- adapters always reinstall); their closed-book settings are below.
RUN npm install -g --no-fund --no-audit \
        "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
        "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && claude --version | grep -F "${CLAUDE_CODE_VERSION}" \
    && codex --version | grep -F "${CODEX_VERSION}"

# --- R packages from a dated Posit Package Manager snapshot ----------------
COPY r-packages.txt install_r_packages.R /opt/tdb/
RUN Rscript /opt/tdb/install_r_packages.R \
        "${CRAN_SNAPSHOT}" "${UBUNTU_CODENAME}" /opt/tdb/r-packages.txt /opt/tdb/r-packages.json

# --- Skills from RConsortium/pharma-skills at a pinned commit --------------
COPY install_skills.sh /opt/tdb/
RUN /opt/tdb/install_skills.sh "${PHARMA_SKILLS_REPO}" "${PHARMA_SKILLS_COMMIT}" /skills

# --- Grader (preinstalled so the verifier needs no PyPI access) ------------
COPY dist/ /tmp/tdb-dist/
RUN set -eux; \
    uv venv /opt/tdb/venv --python /usr/bin/python3; \
    whl="$(ls /tmp/tdb-dist/*.whl 2>/dev/null | tail -n 1 || true)"; \
    if [ -n "${whl}" ]; then \
        uv pip install --python /opt/tdb/venv/bin/python \
            -r /tmp/tdb-dist/requirements.txt "${whl}[judge]"; \
    else \
        uv pip install --python /opt/tdb/venv/bin/python \
            "trialdesignbench[judge]==${TDB_VERSION}"; \
    fi; \
    rm -rf /tmp/tdb-dist; \
    ln -s /opt/tdb/venv/bin/tdb /usr/local/bin/tdb; \
    test "$(tdb --version)" = "${TDB_VERSION}"

COPY check_env.sh /opt/tdb/

# --- Non-root agent user ------------------------------------------------------
RUN useradd --create-home --uid 1001 --shell /bin/bash agent \
    && mkdir -p /app /logs/agent /logs/verifier /logs/artifacts \
    && chown -R agent:agent /app /logs \
    && chmod -R a+rX /skills /opt/tdb

# --- Closed-book settings for agents Harbor installs during setup ----------
# grok-build: root-owned policy pins that no user config can override.
COPY grok-requirements.toml /etc/grok/requirements.toml
# opencode: a model catalog for OPENCODE_MODELS_PATH (the catalog bundled in
# the binary lags behind new models), and its plugin package pre-seeded in
# the global config dir so startup makes no npm registry request.
RUN set -eux; \
    chmod 644 /etc/grok/requirements.toml; \
    curl -fsSL -o /opt/tdb/opencode-models.json https://models.opencode.ai/api.json; \
    python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); assert d["anthropic"]["models"] and d["openai"]["models"]' \
        /opt/tdb/opencode-models.json; \
    chmod 644 /opt/tdb/opencode-models.json
USER agent
RUN set -eux; \
    export HOME=/home/agent; \
    npm install --prefix "${HOME}/.config/opencode" --no-fund --no-audit \
        "@opencode-ai/plugin@${OPENCODE_VERSION}"; \
    npm cache clean --force; \
    test "$(node -p "require('${HOME}/.config/opencode/node_modules/@opencode-ai/plugin/package.json').version")" \
        = "${OPENCODE_VERSION}"
USER root

WORKDIR /app

LABEL org.opencontainers.image.title="trialdesignbench-env" \
      org.opencontainers.image.source="https://github.com/BBSW-org/TrialDesignBench" \
      org.trialdesignbench.version="${TDB_VERSION}" \
      org.trialdesignbench.r-version="${R_VERSION}" \
      org.trialdesignbench.cran-snapshot="${CRAN_SNAPSHOT}" \
      org.trialdesignbench.node-version="${NODE_VERSION}" \
      org.trialdesignbench.uv-version="${UV_VERSION}" \
      org.trialdesignbench.claude-code-version="${CLAUDE_CODE_VERSION}" \
      org.trialdesignbench.codex-version="${CODEX_VERSION}" \
      org.trialdesignbench.grok-build-version="${GROK_BUILD_VERSION}" \
      org.trialdesignbench.opencode-version="${OPENCODE_VERSION}" \
      org.trialdesignbench.pharma-skills-repo="${PHARMA_SKILLS_REPO}" \
      org.trialdesignbench.pharma-skills-commit="${PHARMA_SKILLS_COMMIT}"
