#!/usr/bin/env bash
# Disposable container only. A local ACME fixture issues a real self-signed TLS
# certificate while preserving the install_acme() path and its change to /root.
set -euo pipefail
apt-get update -qq
apt-get install -y -qq curl tar openssl ca-certificates python3 > /dev/null
cat > /tmp/install-acme-fixture.sh <<'BOOTSTRAP'
#!/bin/sh
set -eu
printf '%s\n' "$PWD" > /tmp/acme-install-working-directory
mkdir -p /root/.acme.sh
cp /tmp/acme-fixture.sh /root/.acme.sh/acme.sh
chmod +x /root/.acme.sh/acme.sh
BOOTSTRAP
cat > /tmp/acme-fixture.sh <<'ACME'
#!/usr/bin/env bash
set -euo pipefail
key=''
cert=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key-file) key="$2"; shift 2 ;;
        --fullchain-file) cert="$2"; shift 2 ;;
        *) shift ;;
    esac
done
if [[ -n "$key" && -n "$cert" ]]; then
    openssl req -x509 -newkey rsa:2048 -sha256 -days 1 -nodes \
        -keyout "$key" -out "$cert" -subj /CN=127.0.0.1 \
        -addext subjectAltName=IP:127.0.0.1 > /dev/null 2>&1
fi
ACME
cat > /usr/local/bin/curl <<'CURL'
#!/usr/bin/env bash
args=("$@")
for index in "${!args[@]}"; do
    case "${args[$index]}" in
        https://get.acme.sh)
            args[$index]=file:///tmp/install-acme-fixture.sh ;;
        https://api4.ipify.org)
            printf '127.0.0.1\n200'; exit 0 ;;
        https://github.com/kjxv-1/3x-ui/releases/download/v3.10.2-preset/*)
            args[$index]="file:///assets/${args[$index]##*/}" ;;
        https://raw.githubusercontent.com/kjxv-1/3x-ui/v3.10.2-preset/*)
            raw_path="${args[$index]#https://raw.githubusercontent.com/kjxv-1/3x-ui/}"
            args[$index]="file:///source/${raw_path#*/}" ;;
    esac
done
if [[ " ${args[*]} " == *"%{http_code}"* && " ${args[*]} " == *"I"* ]]; then
    for arg in "${args[@]}"; do
        if [[ "$arg" == file://* ]]; then
            if [[ -f "${arg#file://}" ]]; then printf 200; else printf 404; fi
            exit 0
        fi
    done
fi
# Local release fixtures must report HTTP 200 for GET checksum requests too.
for arg in "${args[@]}"; do
    if [[ "$arg" == file://* && -f "${arg#file://}" ]]; then
        for index in "${!args[@]}"; do
            args[$index]="${args[$index]//\%\{http_code\}/200}"
        done
        break
    fi
done
exec /usr/bin/curl "${args[@]}"
CURL
chmod +x /usr/local/bin/curl
installer=/source/install.sh
if [[ "$XUI_TLS_EXPECT_FAILURE" == 1 ]]; then
    # Remove exactly the directory-restoration fix to prove this fixture detects
    # the user-reported failure, rather than passing by skipping SSL setup.
    python3 - <<'PY'
from pathlib import Path
s=Path('/source/install.sh').read_text()
fix='''    # ACME installation changes the shell directory to the user's home.
    # Restore the bundle directory before Xray reads bin/geoip.dat and before
    # installing the service unit shipped in this same fixed release.
    cd "$xui_folder" || { echo "Cannot enter installed x-ui directory." >&2; exit 1; }
'''
assert s.count(fix) == 1
Path('/tmp/installer-before-fix.sh').write_text(s.replace(fix,''))
PY
    installer=/tmp/installer-before-fix.sh
    if bash "$installer" > /tmp/tls-install.log 2>&1; then
        echo 'FAIL: TLS regression fixture did not detect the missing fix'; exit 1
    fi
    grep -Fq '/root/bin/geoip.dat: no such file or directory' /tmp/tls-install.log
    test "$(cat /tmp/acme-install-working-directory)" = /root
    test -f /etc/x-ui/.preset-pending
    python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/etc/x-ui/x-ui.db')
assert c.execute('SELECT count(*) FROM inbounds').fetchone()[0] == 0
assert c.execute('SELECT count(*) FROM settings WHERE key=?',('tutorialPresetV1',)).fetchone()[0] == 0
PY
    echo 'PASS: reproduced the original geoip path failure after ACME changed to /root; initialization rolled back'
    exit 0
fi
bash "$installer" > /tmp/tls-install.log 2>&1 || { tail -n 30 /tmp/tls-install.log; exit 1; }
test "$(cat /tmp/acme-install-working-directory)" = /root
test ! -e /etc/x-ui/.preset-pending
test -f /etc/systemd/system/x-ui.service
test "$(/usr/local/x-ui/x-ui -v)" = "$(cat /source/internal/config/version)"
. /etc/x-ui/install-result.env
case "$XUI_ACCESS_URL" in https://*) : ;; *) echo 'FAIL: expected HTTPS access URL'; exit 1 ;; esac
test "$(stat -c %a /etc/x-ui/preset-result.json)" = 600
cd /usr/local/x-ui
./x-ui > /tmp/tls-panel.log 2>&1 &
panel_pid=$!
trap 'kill "$panel_pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
    code=$(curl --cacert /root/cert/ip/fullchain.pem -s -o /dev/null -w '%{http_code}' \
        "https://127.0.0.1:${XUI_PANEL_PORT}/${XUI_WEB_BASE_PATH}/" || true)
    case "$code" in 200|301|302|307|308) break ;; esac
    sleep 1
done
case "$code" in 200|301|302|307|308) : ;; *) tail -n 30 /tmp/tls-panel.log; exit 1 ;; esac
python3 - <<'PY'
import json,sqlite3,ssl,urllib.request
c=sqlite3.connect('/etc/x-ui/x-ui.db')
assert c.execute('SELECT count(*) FROM inbounds WHERE enable=1').fetchone()[0] == 2
records=c.execute('SELECT id,email,uuid FROM clients').fetchall()
assert len(records) == 1 and records[0][1] == '01'
assert c.execute('SELECT count(*) FROM client_inbounds WHERE client_id=?',(records[0][0],)).fetchone()[0] == 2
for settings_json,stream_json in c.execute('SELECT settings,stream_settings FROM inbounds'):
    client=json.loads(settings_json)['clients'][0]
    reality=json.loads(stream_json)['realitySettings']
    assert client['id'] == records[0][2] and client['email'] == '01'
    assert reality['minClientVer'] == '1.0.0' and reality['serverNames'] == ['www.samsung.com']
settings=dict(c.execute('SELECT key,value FROM settings'))
assert settings['subEnable'] == settings['subClashEnable'] == 'true'
assert settings['subCertFile'] == settings['webCertFile']
assert settings['subKeyFile'] == settings['webKeyFile']
result=json.load(open('/etc/x-ui/preset-result.json'))
context=ssl.create_default_context(cafile='/root/cert/ip/fullchain.pem')
with urllib.request.urlopen('https://127.0.0.1:2096'+settings['subClashPath']+result['subscriptionId'],context=context,timeout=10) as response:
    text=response.read().decode()
    assert text.count('type: vless') == 2
    assert '自建-直连' in text and '自建-住宅' in text
PY
echo 'PASS: fresh IP-certificate installer restores bundle directory, creates two nodes, installs service, and serves HTTPS panel and Clash subscription'
