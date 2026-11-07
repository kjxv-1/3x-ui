#!/usr/bin/env bash
set -euo pipefail
mkdir -p /usr/local/x-ui /etc/x-ui /root/cert
tar -xzf /assets/x-ui-linux-amd64.tar.gz -C /usr/local
cd /usr/local/x-ui
./x-ui migrate
openssl req -x509 -newkey rsa:2048 -nodes -keyout /root/cert/key.pem -out /root/cert/cert.pem -days 30 -subj /CN=localhost -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' 2>/dev/null
./x-ui setting -username update-fixture -password fixture-only -port 23353 -listenIP 127.0.0.1 -webBasePath /preserved/ \
    -webCert /root/cert/cert.pem -webCertKey /root/cert/key.pem >/dev/null
./x-ui preset -publicAddress 127.0.0.1 -realityServerName www.samsung.com >/tmp/preset.json
sha256sum bin/xray-linux-amd64 /root/cert/*.pem > /tmp/preserved-files.sha256
cp ./x-ui.sh /usr/bin/x-ui
# Retain an executable baseline wrapper while serving the actual packaged panel.
mv x-ui x-ui.real
cat > x-ui <<'BASELINE'
#!/usr/bin/env bash
if [[ "${1:-}" == -v ]]; then echo 3.10.2-preset-ipv6-hy2
else exec /usr/local/x-ui/x-ui.real "$@"; fi
BASELINE
chmod +x x-ui
cat > /usr/local/bin/systemctl <<'SERVICE'
#!/usr/bin/env bash
set -euo pipefail
alive() {
    [[ -f /run/xui-fixture.pid ]] || return 1
    pid=$(cat /run/xui-fixture.pid)
    kill -0 "$pid" 2>/dev/null && [[ "$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)" != Z ]]
}
case "$1" in
    cat|show) exit 0 ;;
    start)
        cd /usr/local/x-ui
        nohup ./x-ui 9>&- >/tmp/update-panel.log 2>&1 &
        echo "$!" > /run/xui-fixture.pid ;;
    stop)
        if alive; then kill "$pid"; fi
        for _ in $(seq 1 30); do if ! alive; then break; fi; sleep 0.2; done ;;
    is-active) alive ;;
    *) exit 1 ;;
esac
SERVICE
cat > /usr/local/bin/curl <<'DOWNLOAD'
#!/usr/bin/env bash
args=("$@")
for i in "${!args[@]}"; do
    case "${args[$i]}" in
        https://github.com/kjxv-1/3x-ui/releases/download/v3.10.2-preset-sort/*)
            args[$i]="file:///assets/${args[$i]##*/}" ;;
    esac
done
exec /usr/bin/curl "${args[@]}"
DOWNLOAD
chmod +x /usr/local/bin/systemctl /usr/local/bin/curl
systemctl start x-ui
trap 'systemctl stop x-ui' EXIT
for _ in $(seq 1 30); do
    if curl -kfLsS https://127.0.0.1:23353/preserved/ >/dev/null 2>&1; then break; fi
    sleep 1
done
curl -kfLsS https://127.0.0.1:23353/preserved/ >/dev/null
cat > /tmp/snapshot.py <<'SNAPSHOT'
import json, sqlite3
with sqlite3.connect('/etc/x-ui/x-ui.db') as c:
    inbounds = c.execute('SELECT id,remark,port,tag,settings,stream_settings,sub_sort_index FROM inbounds ORDER BY id').fetchall()
    nodes=[]
    for row in inbounds:
        row=list(row)
        settings=json.loads(row[4])
        for client in settings.get('clients',[]): client.pop('updated_at',None)
        row[4]=settings
        row[5]=json.loads(row[5])
        nodes.append(row)
    client_rows=[]
    c.row_factory=sqlite3.Row
    for row in c.execute('SELECT * FROM clients ORDER BY id'):
        client_rows.append({k:row[k] for k in row.keys() if k not in ('updated_at','last_online')})
    settings=c.execute('SELECT key,value FROM settings WHERE key IN (?,?,?,?,?,?,?,?,?,?) ORDER BY key',
        ('webPort','webBasePath','webCertFile','webKeyFile','subPath','subClashPath','remarkTemplate','subInfoNodeEnable','subClashEnable','tutorialPresetV1')).fetchall()
    json.dump({'nodes':nodes,'clients':client_rows,'settings':[list(r) for r in settings]},open('/tmp/'+__import__('sys').argv[1]+'.json','w'),sort_keys=True)
SNAPSHOT
python3 /tmp/snapshot.py before
bash /source/deploy/preset/update.sh
test "$(./x-ui -v)" = 3.10.2-preset-sort
curl -kfLsS https://127.0.0.1:23353/preserved/ >/dev/null
python3 /tmp/snapshot.py after
cmp /tmp/before.json /tmp/after.json
sha256sum -c /tmp/preserved-files.sha256
test -n "$(find /var/backups/3x-ui -name x-ui.db -type f)"
echo 'PASS: actual panel updated, HTTPS login reachable, all node/client/subscription/certificate/core settings preserved.'
