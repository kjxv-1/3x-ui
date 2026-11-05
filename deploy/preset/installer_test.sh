#!/usr/bin/env bash
# Exercise the delivered installer in a disposable container before publishing.
# Release URLs resolve to the just-built local assets; other requests are real.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
assets="$(realpath "${1:?package directory required}")"
docker run --rm -v "$root:/source:ro" -v "$assets:/assets:ro" \
    -e XUI_PRESET_REALITY_SERVER_NAME=www.samsung.com -e XUI_NONINTERACTIVE=1 -e XUI_SSL_MODE=none -e XUI_ENABLE_FAIL2BAN=false \
    -e XUI_PRESET_PUBLIC_ADDRESS=127.0.0.1 -e DEBIAN_FRONTEND=noninteractive \
    ubuntu:24.04 bash -euo pipefail -c '
trap "echo Installer_fixture_failed_at_line_\$LINENO >&2" ERR
apt-get update -qq
apt-get install -y -qq curl tar openssl ca-certificates python3 > /dev/null
cat > /usr/local/bin/curl <<"CURL"
#!/usr/bin/env bash
args=("$@")
for index in "${!args[@]}"; do
    case "${args[$index]}" in
        https://github.com/kjxv-1/3x-ui/releases/download/v3.10.2-preset-ipv6-hy2/*)
            args[$index]="file:///assets/${args[$index]##*/}" ;;
        https://raw.githubusercontent.com/kjxv-1/3x-ui/v3.10.2-preset-ipv6-hy2/*)
            args[$index]="file:///source/${args[$index]#https://raw.githubusercontent.com/kjxv-1/3x-ui/v3.10.2-preset-ipv6-hy2/}" ;;
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
if XUI_PRESET_DIRECT_PORT=65536 bash /source/install.sh > /tmp/failed-install.log 2>&1; then
    echo "Invalid preset port unexpectedly succeeded"; exit 1
fi
test -f /etc/x-ui/.preset-pending
test ! -e /etc/x-ui/.kjxv-preset-pending
python3 - <<"PY"
import sqlite3
c=sqlite3.connect("/etc/x-ui/x-ui.db")
assert c.execute("SELECT count(*) FROM inbounds").fetchone()[0] == 0
assert c.execute("SELECT count(*) FROM settings WHERE key=?", ("tutorialPresetV1",)).fetchone()[0] == 0
PY
bash /source/install.sh > /tmp/install.log 2>&1 || { tail -n 60 /tmp/install.log; exit 1; }
test ! -e /etc/x-ui/.preset-pending
test ! -e /etc/x-ui/.kjxv-preset-pending
test "$(/usr/local/x-ui/x-ui -v)" = "3.10.2-preset-ipv6-hy2"
test "$(stat -c %a /etc/x-ui/install-result.env)" = 600
test "$(stat -c %a /etc/x-ui/preset-result.json)" = 600
. /etc/x-ui/install-result.env
test -n "$XUI_USERNAME" && test "$XUI_USERNAME" != admin
test -n "$XUI_PASSWORD" && test "$XUI_PASSWORD" != admin
/usr/local/x-ui/x-ui setting -show | grep -q "hasDefaultCredential: false"
python3 - <<"PY"
import json,sqlite3
result=json.load(open("/etc/x-ui/preset-result.json"))
c=sqlite3.connect("/etc/x-ui/x-ui.db")
assert c.execute("SELECT count(*) FROM inbounds WHERE enable=1").fetchone()[0] == 2 + int(result["ipv6Enabled"]) + int(result["hy2Enabled"])
records=c.execute("SELECT id,email,uuid FROM clients").fetchall()
assert len(records) == 1 and records[0][1] == "01"
assert c.execute("SELECT count(*) FROM client_inbounds WHERE client_id=?",(records[0][0],)).fetchone()[0] == 4
for row in c.execute("SELECT settings,stream_settings FROM inbounds"):
    client=json.loads(row[0])["clients"][0]
    if "realitySettings" not in json.loads(row[1]):
        assert len(client["auth"]) == 64
        continue
    reality=json.loads(row[1])["realitySettings"]
    assert client["email"] == "01" and client["id"] == records[0][2]
    assert reality["minClientVer"] == "1.0.0" and reality["serverNames"] == ["www.samsung.com"]
sub_ids=[json.loads(row[0])["clients"][0]["subId"] for row in c.execute("SELECT settings FROM inbounds")]
assert sub_ids == [result["subscriptionId"]] * 4
assert c.execute("SELECT value FROM settings WHERE key=?",("subClashEnable",)).fetchone()[0] == "true"
assert c.execute("SELECT value FROM settings WHERE key=?",("subInfoNodeEnable",)).fetchone()[0] == "true"
template=c.execute("SELECT value FROM settings WHERE key=?",("remarkTemplate",)).fetchone()[0]
assert "📊 剩余流量：" in template and "📅 到期：" in template
PY
cd /usr/local/x-ui
./x-ui > /tmp/panel.log 2>&1 &
panel_pid=$!
trap "kill $panel_pid 2>/dev/null || true" EXIT
for _ in $(seq 1 30); do
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${XUI_PANEL_PORT}/${XUI_WEB_BASE_PATH}/" || true)
    case "$code" in 200|301|302|307|308) break ;; esac
    sleep 1
done
case "$code" in 200|301|302|307|308) : ;; *) tail -n 60 /tmp/panel.log; exit 1 ;; esac
python3 - <<"PY"
import json,sqlite3,urllib.request
result=json.load(open("/etc/x-ui/preset-result.json"))
path=sqlite3.connect("/etc/x-ui/x-ui.db").execute("SELECT value FROM settings WHERE key=?",("subClashPath",)).fetchone()[0]
with urllib.request.urlopen("http://127.0.0.1:2096"+path+result["subscriptionId"],timeout=10) as r:
    text=r.read().decode()
    assert text.count("type: vless") == 2 + int(result["ipv6Enabled"]), "Unexpected Clash body: " + repr(text[:3000])
    assert "IPv4-直连" in text and "住宅-待配置" in text
PY
kill "$panel_pid"
wait "$panel_pid" || true
trap - EXIT
python3 - <<"PY"
import json,sqlite3
c=sqlite3.connect("/etc/x-ui/x-ui.db")
config=json.loads(c.execute("SELECT value FROM settings WHERE key=?",("xrayTemplateConfig",)).fetchone()[0])
for ob in config["outbounds"]:
    if ob["tag"] == "默认":
        ob["settings"]={"address":"192.0.2.10","port":1080,"user":"saved-user","pass":"saved-password"}
c.execute("UPDATE settings SET value=? WHERE key=?",(json.dumps(config),"xrayTemplateConfig"))
c.execute("UPDATE settings SET value=? WHERE key=?",("false","subClashEnable"))
c.commit()
snapshot={"nodes":c.execute("SELECT tag,settings,stream_settings FROM inbounds ORDER BY tag").fetchall(),
          "settings":c.execute("SELECT key,value FROM settings WHERE key IN (?,?,?,?,?) ORDER BY key",("xrayTemplateConfig","subClashEnable","tutorialPresetV1","subInfoNodeEnable","remarkTemplate")).fetchall()}
json.dump(snapshot,open("/tmp/before.json","w"))
PY
bash /source/install.sh > /tmp/reinstall.log 2>&1 || { tail -n 60 /tmp/reinstall.log; exit 1; }
python3 - <<"PY"
import json,sqlite3
c=sqlite3.connect("/etc/x-ui/x-ui.db")
snapshot={"nodes":c.execute("SELECT tag,settings,stream_settings FROM inbounds ORDER BY tag").fetchall(),
          "settings":c.execute("SELECT key,value FROM settings WHERE key IN (?,?,?,?,?) ORDER BY key",("xrayTemplateConfig","subClashEnable","tutorialPresetV1","subInfoNodeEnable","remarkTemplate")).fetchall()}
before=json.load(open("/tmp/before.json"))
def canonical(snapshot):
    nodes=[]
    for tag,settings,stream in snapshot["nodes"]:
        settings=json.loads(settings)
        # Background client synchronization refreshes audit timestamps.
        # Compare every persistent connection setting, excluding only that clock.
        for client in settings["clients"]:
            client.pop("updated_at",None)
        nodes.append([tag,settings,json.loads(stream)])
    return {"nodes":nodes,"settings":[[key,value if key == "remarkTemplate" else json.loads(value)] for key,value in snapshot["settings"]]}
assert canonical(snapshot) == canonical(before), "Reinstall changed persistent connection settings"
PY
echo "PASS: full installer, failed-first-install retry, random admin credentials, two-node Clash subscription, repeat install preserves residential credentials and subscription settings."
'
