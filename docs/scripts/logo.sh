#!/bin/zsh

# Generate docs/assets/logo.svg and docs/assets/favicon.svg with logo.py.
# Inputs in docs/scripts: logo.py, SpecialGothicCondensedOne-Regular.ttf
# (SIL OFL 1.1, see SpecialGothicCondensedOne-OFL.txt).

set -eu

uv run \
	--no-project \
	--with fonttools==4.65.0 \
	python "$(dirname "$0")/logo.py"
