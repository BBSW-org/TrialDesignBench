#!/usr/bin/env bash
# Print the versions baked into the environment image as JSON-ish lines and
# fail if any required component is missing. Run by `tdb env check`.
set -uo pipefail

status=0
report() { printf '%-22s %s\n' "$1" "$2"; }

report "tdb" "$(tdb --version 2>&1 || { status=1; echo MISSING; })"
report "python" "$(python3 --version 2>&1 || { status=1; echo MISSING; })"
report "uv" "$(uv --version 2>&1 || { status=1; echo MISSING; })"
report "node" "$(node --version 2>&1 || { status=1; echo MISSING; })"
report "claude" "$(claude --version 2>&1 || { status=1; echo MISSING; })"
report "codex" "$(codex --version 2>&1 || { status=1; echo MISSING; })"
report "skills" "$(ls /skills 2>/dev/null | tr '\n' ' ' || { status=1; echo MISSING; })"
report "skills commit" "$(cat /skills/.commit 2>/dev/null || { status=1; echo MISSING; })"

Rscript -e '
pkgs <- trimws(readLines("/opt/tdb/r-packages.txt"))
pkgs <- pkgs[nzchar(pkgs)]
cat(sprintf("%-22s %s\n", "R", as.character(getRversion())))
ok <- TRUE
for (p in pkgs) {
  v <- tryCatch(as.character(packageVersion(p)), error = function(e) NA)
  if (is.na(v) || !requireNamespace(p, quietly = TRUE)) { ok <- FALSE; v <- "MISSING" }
  cat(sprintf("%-22s %s\n", paste0("R::", p), v))
}
if (!ok) quit(status = 1)
' || status=1

id agent >/dev/null 2>&1 || { echo "user agent: MISSING"; status=1; }
exit "${status}"
