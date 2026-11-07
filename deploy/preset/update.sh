#!/usr/bin/env bash
# Update an existing standard SQLite installation without rerunning initialization.
set -Eeuo pipefail
umask 077
readonly tag='v3.10.2-preset-sort'
readonly repo='kjxv-1/3x-ui'
# A prefix lets the offline tests exercise the real script without touching /etc.
root="$(realpath -m "${XUI_UPDATE_ROOT:-/}")"
root="${root%/}"
[[ -n "$root" || "$EUID" == 0 ]] || { echo '请以 root 身份执行更新。' >&2; exit 1; }
for tool in curl tar sha256sum python3 flock timeout install cp mv; do
    command -v "$tool" >/dev/null || { echo "缺少必要工具：$tool；尚未修改面板。" >&2; exit 1; }
done
readonly app="$root/usr/local/x-ui"
readonly db="$root/etc/x-ui"
readonly menu="$root/usr/bin/x-ui"
readonly backups="$root/var/backups/3x-ui"
[[ -x "$app/x-ui" && -s "$db/x-ui.db" ]] || {
    echo '未找到标准路径的已有面板和 SQLite 数据库。请用安装命令部署；本更新命令不会新建面板。' >&2; exit 1;
}
current="$(timeout 15 "$app/x-ui" -v)"
[[ "$current" == 3.10.2* ]] || {
    echo "当前面板版本 $current；此命令只更新 3.10.2 系列，尚未修改。" >&2; exit 1;
}
if command -v systemctl >/dev/null && systemctl cat x-ui >/dev/null 2>&1; then
    manager=systemd
elif command -v rc-service >/dev/null && [[ -f "$root/etc/init.d/x-ui" ]]; then
    manager=openrc
else
    echo '未检测到 systemd/OpenRC 管理的 x-ui 服务；Docker 请通过容器方式升级。' >&2; exit 1
fi
service_action() {
    if [[ "$manager" == systemd ]]; then systemctl "$1" x-ui
    else rc-service x-ui "$1"; fi
}
service_running() {
    if [[ "$manager" == systemd ]]; then systemctl is-active --quiet x-ui
    else rc-service x-ui status >/dev/null 2>&1; fi
}
mkdir -p "$backups"
chmod 700 "$backups"
exec 9>"$backups/.update.lock"
flock -n 9 || { echo '另一个更新正在运行，请稍后重试。' >&2; exit 1; }
work="$(mktemp -d "$backups/.download.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT
if [[ "$manager" == systemd ]]; then
    systemctl show x-ui -p Environment --value > "$work/service-environment"
else : > "$work/service-environment"; fi
# Refuse nonstandard database locations/backends before stopping the service.
python3 - "$root" "$work/service-environment" <<'PY'
import os, pathlib, shlex, sqlite3, sys
root, env_path = sys.argv[1:]
settings = {}
for name in ('/etc/default/x-ui', '/etc/conf.d/x-ui', '/etc/sysconfig/x-ui'):
    p = pathlib.Path(root + name)
    if p.exists():
        for line in p.read_text().splitlines():
            for token in shlex.split(line, comments=True):
                if '=' in token:
                    key, value = token.split('=', 1)
                    settings[key] = value
for token in shlex.split(pathlib.Path(env_path).read_text()):
    if '=' in token:
        key, value = token.split('=', 1)
        settings[key] = value
for key in ('XUI_DB_TYPE', 'XUI_DB_FOLDER'):
    if key in os.environ:
        settings[key] = os.environ[key]
if settings.get('XUI_DB_TYPE', 'sqlite').lower() not in ('', 'sqlite') or settings.get('XUI_DB_FOLDER', '/etc/x-ui') not in ('', '/etc/x-ui'):
    sys.exit('此更新命令支持标准 /etc/x-ui 的 SQLite 安装；自定义数据库需单独制定更新方案，尚未修改。')
with sqlite3.connect('file:' + root + '/etc/x-ui/x-ui.db?mode=ro', uri=True) as c:
    c.execute('SELECT key, value FROM settings LIMIT 1').fetchall()
    c.execute('SELECT id FROM inbounds LIMIT 1').fetchall()
PY
case "$(uname -m)" in
    x86_64|amd64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;;
    armv7*) arch=armv7 ;; armv6*) arch=armv6 ;; armv5*) arch=armv5 ;;
    i386|i486|i586|i686) arch=386 ;; s390x) arch=s390x ;;
    *) echo '当前 CPU 架构没有本版安装包。' >&2; exit 1 ;;
esac
package="x-ui-linux-$arch.tar.gz"
url="https://github.com/$repo/releases/download/$tag/$package"
echo "正在下载并校验 $tag，当前面板继续运行……"
curl -fLsS --retry 3 --connect-timeout 15 --max-time 900 "$url" -o "$work/package.tar.gz"
curl -fLsS --retry 3 --connect-timeout 15 --max-time 60 "$url.sha256" -o "$work/checksum"
expected="$(awk 'NR==1 {print $1}' "$work/checksum")"
[[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || { echo '安装包校验文件无效。' >&2; exit 1; }
printf '%s  %s\n' "$expected" "$work/package.tar.gz" | sha256sum -c - >/dev/null
# Stream only these two regular file contents; never extract paths from an archive.
tar -xOzf "$work/package.tar.gz" x-ui/x-ui > "$work/x-ui"
tar -xOzf "$work/package.tar.gz" x-ui/x-ui.sh > "$work/x-ui.sh"
chmod 755 "$work/x-ui" "$work/x-ui.sh"
[[ "$(timeout 15 "$work/x-ui" -v)" == "${tag#v}" ]] || { echo '安装包面板版本不匹配。' >&2; exit 1; }
bash -n "$work/x-ui.sh"
backup="$backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -m 700 "$backup"
printf '%s\n' "$current" > "$backup/previous-version"
was_running=0; service_running && was_running=1
stopped=0; changed=0; complete_backup=0
rollback() {
    result=$?
    [[ "$result" != 0 ]] || result=1
    trap - ERR INT TERM
    set +e
    if [[ "$stopped" == 1 ]]; then
        echo '更新未通过启动检查，正在恢复原版本……' >&2
        failed=0
        service_action stop >/dev/null 2>&1 || failed=1
        if [[ "$changed" == 1 && "$complete_backup" == 1 ]]; then
            cp -a "$backup/x-ui" "$app/x-ui.restore" && mv -f "$app/x-ui.restore" "$app/x-ui" || failed=1
            if [[ -f "$backup/x-ui.sh" ]]; then cp -a "$backup/x-ui.sh" "$app/x-ui.sh" || failed=1; fi
            if [[ -f "$backup/menu" ]]; then cp -a "$backup/menu" "$menu" || failed=1; fi
            rm -f -- "$db/x-ui.db-wal" "$db/x-ui.db-shm"
            cp -a "$backup/database/." "$db/" || failed=1
        fi
        if [[ "$was_running" == 1 ]]; then service_action start >/dev/null 2>&1 || failed=1; fi
        if [[ "$failed" == 0 ]]; then echo '已恢复原程序、数据库和原服务状态。' >&2
        else echo "自动恢复未全部完成；请保留备份并检查服务：$backup" >&2; fi
    fi
    echo "更新失败；备份目录：$backup" >&2
    exit "${result:-1}"
}
trap rollback ERR INT TERM
echo '下载校验完成，正在短暂停机并备份……'
service_action stop
stopped=1
cp -a "$app/x-ui" "$backup/x-ui"
[[ ! -f "$app/x-ui.sh" ]] || cp -a "$app/x-ui.sh" "$backup/x-ui.sh"
[[ ! -f "$menu" ]] || cp -a "$menu" "$backup/menu"
cp -a "$db" "$backup/database"
complete_backup=1
changed=1
install -m 755 "$work/x-ui" "$app/x-ui.new"
mv -f "$app/x-ui.new" "$app/x-ui"
install -m 755 "$work/x-ui.sh" "$app/x-ui.sh"
install -m 755 "$work/x-ui.sh" "$menu"
# Build a local, credential-free login probe from the preserved database.
python3 - "$db/x-ui.db" > "$work/health-url" <<'PY'
import ipaddress, sqlite3, sys
with sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True) as c:
    s = dict(c.execute('SELECT key, value FROM settings'))
host = s.get('webListen', '') or '127.0.0.1'
if host in ('0.0.0.0', '::'): host = '127.0.0.1' if host == '0.0.0.0' else '::1'
ip = ipaddress.ip_address(host)
host = '[' + str(ip) + ']' if ip.version == 6 else str(ip)
port = int(s.get('webPort', '2053'))
assert 1 <= port <= 65535
path = s.get('webBasePath', '/') or '/'
assert path.startswith('/') and not any(c.isspace() for c in path)
scheme = 'https' if s.get('webCertFile', '') and s.get('webKeyFile', '') else 'http'
print(f'{scheme}://{host}:{port}{path.rstrip("/")}/')
PY
service_action start
healthy=0
for _ in $(seq 1 30); do
    if service_running; then
        code="$(curl --noproxy '*' -k -sS --max-time 2 -o /dev/null -w '%{http_code}' "$(cat "$work/health-url")" 2>/dev/null || true)"
        if [[ "$code" =~ ^(200|301|302|303|307|308)$ ]]; then healthy=1; break; fi
    fi
    sleep 1
done
[[ "$healthy" == 1 ]] || { echo '新版服务或本地登录页面检查失败。' >&2; false; }
trap - ERR INT TERM
echo "更新成功：$current → ${tag#v}"
echo "备份目录：$backup"
echo '节点、客户端、订阅地址、证书和原内核已保留。请在浏览器按 Ctrl+F5 刷新，然后进入「入站列表 → 订阅排序」。'
