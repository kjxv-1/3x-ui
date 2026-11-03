#!/usr/bin/env bash
# Reproduce the ACME directory change and then validate a fresh TLS install.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
assets="$(realpath "${1:?package directory required}")"
for scenario in regression ipv4 dual; do
    expect_failure=0
    [[ "$scenario" != regression ]] || expect_failure=1
    docker run --rm --sysctl net.ipv6.conf.all.disable_ipv6=0 -v "$root:/source:ro" -v "$assets:/assets:ro" \
        -e XUI_PRESET_REALITY_SERVER_NAME=www.samsung.com -e XUI_NONINTERACTIVE=1 -e XUI_SSL_MODE=ip \
        -e XUI_ENABLE_FAIL2BAN=false -e XUI_PRESET_PUBLIC_ADDRESS=127.0.0.1 \
        -e XUI_TLS_EXPECT_FAILURE="$expect_failure" -e XUI_TLS_SCENARIO="$scenario" -e DEBIAN_FRONTEND=noninteractive \
        ubuntu:24.04 bash /source/deploy/preset/tls_installer_fixture.sh
done
