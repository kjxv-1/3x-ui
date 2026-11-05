#!/usr/bin/env bash
set -euo pipefail
readonly PRESET_TAG="v3.6.0-preset.4"
task_installer=$(mktemp)
trap 'rm -f "$task_installer"' EXIT
curl -fLsS --retry 5 "https://raw.githubusercontent.com/kjxv-1/3x-ui/${PRESET_TAG}/install.sh" -o "$task_installer"
bash "$task_installer" "$PRESET_TAG"
