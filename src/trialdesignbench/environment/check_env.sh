#!/usr/bin/env bash
# Print the versions baked into the environment image as JSON-ish lines and
# fail if any required component is missing. Run by `tdb env check`.
set -uo pipefail

status=0
# Values come from command substitutions (subshells), so a missing component
# is signalled by the value MISSING rather than by setting `status` there.
report() {
    printf '%-22s %s\n' "$1" "$2"
    case "$2" in *MISSING*) status=1 ;; esac
}

report "tdb" "$(tdb --version 2>&1 || echo MISSING)"
report "python" "$(python3 --version 2>&1 || echo MISSING)"
report "uv" "$(uv --version 2>&1 || echo MISSING)"
report "node" "$(node --version 2>&1 || echo MISSING)"
report "claude" "$(claude --version 2>&1 || echo MISSING)"
report "codex" "$(codex --version 2>&1 || echo MISSING)"
report "skills" "$(ls /skills 2>/dev/null | tr '\n' ' ' || echo MISSING)"
report "skills commit" "$(cat /skills/.commit 2>/dev/null || echo MISSING)"

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
