#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
assets="$(realpath "${1:?package directory required}")"
docker run --rm -v "$root:/source:ro" -v "$assets:/assets:ro" ubuntu:24.04 \
    bash -euo pipefail -c '
        apt-get update -qq
        apt-get install -y -qq curl tar openssl ca-certificates python3 util-linux >/dev/null
        bash /source/deploy/preset/updater_fixture.sh
    '
