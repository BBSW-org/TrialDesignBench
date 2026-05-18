#!/bin/zsh

# Sync README.md with modified image path for docs/index.md
awk '{gsub("https://github.com/BBSW-org/TrialDesignBench/raw/main/docs/assets/logo.svg", "assets/logo.svg"); print}' README.md >docs/index.md

# Sync CHANGELOG.md with docs/changelog.md
cp CHANGELOG.md docs/changelog.md
