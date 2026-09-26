#!/usr/bin/env bash
# Install agent skills from a git repository at a pinned commit.
# Every top-level directory with a SKILL.md becomes /skills/<name>.
#
# Usage: install_skills.sh <repo-url> <commit-sha> <dest>
set -euo pipefail

repo="$1"
commit="$2"
dest="$3"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

git init -q "${tmp}"
git -C "${tmp}" remote add origin "${repo}"
git -C "${tmp}" fetch -q --depth 1 origin "${commit}"
git -C "${tmp}" checkout -q FETCH_HEAD
actual="$(git -C "${tmp}" rev-parse HEAD)"
if [ "${actual}" != "${commit}" ]; then
    echo "expected commit ${commit}, got ${actual}" >&2
    exit 1
fi

mkdir -p "${dest}"
for dir in "${tmp}"/*/; do
    name="$(basename "${dir}")"
    case "${name}" in _* | .*) continue ;; esac
    if [ -f "${dir}/SKILL.md" ]; then
        cp -RL "${dir}" "${dest}/${name}"
    fi
done
printf '%s\n' "${commit}" >"${dest}/.commit"

test -f "${dest}/group-sequential-design/SKILL.md"
echo "installed skills:" && ls -1 "${dest}"
