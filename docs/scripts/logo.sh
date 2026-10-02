#!/bin/zsh

# Generate docs/assets/logo.svg and docs/assets/favicon.svg with logo.py.
# Inputs in docs/scripts: logo.py, BarlowCondensed-Medium.ttf
# (SIL OFL 1.1, see BarlowCondensed-OFL.txt).

set -eu

uv run \
	--no-project \
	--with fonttools==4.66.0 \
	python "$(dirname "$0")/logo.py"
