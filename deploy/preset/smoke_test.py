"""Disposable Linux integration test: native preset, subscription and SOCKS routes.

The fixture only permits loopback HTTP destinations inside its temporary DB;
the delivered profile's private-address protection is asserted by Go tests.
"""
import argparse
import contextlib
import http.server
import json
import os
import pathlib
import re
import select
import socket
import socketserver
import sqlite3
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


def exact(stream, size):
    result = b''
    while len(result) < size:
        value = stream.recv(size - len(result))
        if not value:
            raise ConnectionError('Connection closed')
        result += value
    return result


class HTTP(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        body = b'preset-route-ok'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Socks(socketserver.BaseRequestHandler):
    connections = 0

    def handle(self):
        with contextlib.suppress(OSError, ConnectionError):
            self.request.settimeout(8)
            version, methods = exact(self.request, 2)
            assert version == 5 and 2 in exact(self.request, methods)
            self.request.sendall(b'\x05\x02')
            assert exact(self.request, 1) == b'\x01'
            username = exact(self.request, exact(self.request, 1)[0])
            password = exact(self.request, exact(self.request, 1)[0])
            assert username == b'fixture-user' and password == b'fixture-password'
            self.request.sendall(b'\x01\x00')
            version, command, _, address_type = exact(self.request, 4)
            assert version == 5 and command == 1
            if address_type == 1:
                host = socket.inet_ntoa(exact(self.request, 4))
            elif address_type == 3:
                host = exact(self.request, exact(self.request, 1)[0]).decode()
            else:
                raise ValueError('Unexpected fixture address type')
            destination_port = struct.unpack('!H', exact(self.request, 2))[0]
            assert host == '127.0.0.1'
            with socket.create_connection((host, destination_port), timeout=8) as target:
                type(self).connections += 1
                self.request.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
                while True:
                    ready, _, _ = select.select([self.request, target], [], [], 8)
                    if not ready:
                        return
                    for readable in ready:
                        value = readable.recv(65536)
                        if not value:
                            return
                        (target if readable is self.request else self.request).sendall(value)


class TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def http_through_socks(proxy_port, destination_port):
    with socket.create_connection(('127.0.0.1', proxy_port), timeout=5) as stream:
        stream.settimeout(5)
        stream.sendall(b'\x05\x01\x00')
        assert exact(stream, 2) == b'\x05\x00'
        stream.sendall(b'\x05\x01\x00\x01\x7f\x00\x00\x01' + struct.pack('!H', destination_port))
        response = exact(stream, 4)
        if response[1] != 0:
            raise ConnectionError('Proxy refused connection')
        if response[3] == 1:
            exact(stream, 6)
        elif response[3] == 4:
            exact(stream, 18)
        else:
            exact(stream, exact(stream, 1)[0] + 2)
        stream.sendall(b'GET / HTTP/1.1\r\nHost: fixture.local\r\nConnection: close\r\n\r\n')
        response = b''
        while value := stream.recv(65536):
            response += value
        return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    root = pathlib.Path(args.directory).resolve()
    processes = []
    servers = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='preset-smoke-') as temporary:
        work = pathlib.Path(temporary)
        env = dict(os.environ, XUI_DB_FOLDER=str(work), XUI_BIN_FOLDER=str(root / 'bin'),
                   XRAY_LOCATION_ASSET=str(root / 'bin'), XUI_ENABLE_FAIL2BAN='false', XUI_LOG_LEVEL='error')
        binary = str(root / ('x-ui.exe' if os.name == 'nt' else 'x-ui'))
        direct_port, residential_port, panel_port, subscription_port = port(), port(), port(), port()
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(work / 'key.pem'),
                        '-out', str(work / 'cert.pem'), '-days', '1', '-subj', '/CN=www.samsung.com',
                        '-addext', 'subjectAltName=DNS:www.samsung.com,IP:127.0.0.1,IP:::1'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([binary, 'cert', '-webCert', str(work / 'cert.pem'), '-webCertKey', str(work / 'key.pem')],
                       cwd=root, env=env, check=True, stdout=subprocess.DEVNULL)
        command = [binary, 'preset', '-realityServerName', 'www.samsung.com', '-publicAddress', '127.0.0.1', '-directPort', str(direct_port), '-residentialPort', str(residential_port), '-ipv6Address', '2001:db8::10']
        subprocess.run(command, cwd=root, env=env, check=True, stdout=subprocess.DEVNULL)
        database = sqlite3.connect(work / 'x-ui.db')
        result = json.loads(database.execute("SELECT value FROM settings WHERE key='tutorialPresetV1'").fetchone()[0])
        clash_path = database.execute("SELECT value FROM settings WHERE key='subClashPath'").fetchone()[0]
        inbounds = database.execute('SELECT tag, settings, stream_settings FROM inbounds ORDER BY sub_sort_index').fetchall()
        assert [row[0] for row in inbounds] == ['默认直连', '默认住宅', '默认IPv6直连', '默认HY2直连']
        assert 'kjxv' not in json.dumps(inbounds).lower()
        records = database.execute('SELECT id,email,uuid,sub_id FROM clients').fetchall()
        assert len(records) == 1 and records[0][1] == '01'
        attachments = database.execute('SELECT inbound_id FROM client_inbounds WHERE client_id=?', (records[0][0],)).fetchall()
        assert len(attachments) == 4
        for tag, settings_json, stream_json in inbounds:
            client = json.loads(settings_json)['clients'][0]
            assert client['email'] == '01' and client['id'] == records[0][2] and client['subId'] == result['subscriptionId']
            if tag == '默认HY2直连':
                assert len(client['auth']) == 64
                continue
            reality = json.loads(stream_json)['realitySettings']
            assert reality['minClientVer'] == '1.0.0'
            assert reality['serverNames'] == ['www.samsung.com']
        core = str(root / ('bin/xray-windows-amd64.exe' if os.name == 'nt' else 'bin/xray-linux-amd64'))
        assert '26.6.27' in subprocess.check_output([core, 'version'], env=env, text=True)
        http_fixture = start_server(http.server.ThreadingHTTPServer(('127.0.0.1', 0), HTTP))
        servers.append(http_fixture)
        socks = start_server(TCPServer(('127.0.0.1', 0), Socks))
        servers.append(socks)
        # A local TLS 1.3 / h2 target makes REALITY handshake tests deterministic.
        target = http.server.ThreadingHTTPServer(('127.0.0.1', 0), HTTP)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_3
        tls.set_alpn_protocols(['h2'])
        tls.load_cert_chain(work / 'cert.pem', work / 'key.pem')
        target.socket = tls.wrap_socket(target.socket, server_side=True)
        servers.append(start_server(target))
        for tag, _, stream_json in inbounds:
            if tag == '默认HY2直连':
                continue
            stream = json.loads(stream_json)
            stream['realitySettings']['target'] = f'127.0.0.1:{target.server_address[1]}'
            database.execute('UPDATE inbounds SET stream_settings=? WHERE tag=?', (json.dumps(stream), tag))
        config = json.loads(database.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0])
        assert 'kjxv' not in json.dumps(config).lower()
        assert any(ob['tag'] == '默认' and ob['protocol'] == 'socks' for ob in config['outbounds'])
        assert config['routing']['rules'][-1]['inboundTag'] == ['默认住宅']
        assert config['routing']['rules'][-1]['outboundTag'] == '默认'
        config['routing']['rules'] = [rule for rule in config['routing']['rules'] if rule.get('ip') != ['geoip:private']]
        for outbound in config['outbounds']:
            if outbound['tag'] == 'direct':
                outbound['settings']['finalRules'] = [{'action': 'allow'}]
            if outbound['tag'] == '默认':
                outbound['settings'] = {'address': '127.0.0.1', 'port': socks.server_address[1], 'user': 'fixture-user', 'pass': 'fixture-password'}
        values = {'xrayTemplateConfig': json.dumps(config), 'webPort': str(panel_port), 'webListen': '127.0.0.1',
                  'webBasePath': '/', 'webCertFile': '', 'webKeyFile': '', 'subCertFile': '', 'subKeyFile': '', 'subPort': str(subscription_port), 'subListen': '127.0.0.1'}
        for key, value in values.items():
            if database.execute('UPDATE settings SET value=? WHERE key=?', (value, key)).rowcount == 0:
                database.execute('INSERT INTO settings(key,value) VALUES (?,?)', (key, value))
        database.commit()
        token_output = subprocess.check_output([binary, 'setting', '-getApiToken', 'true'], cwd=root, env=env, text=True)
        token = re.search(r'apiToken:\s*(\S+)', token_output).group(1)
        log = (work / 'runtime.log').open('wb')
        try:
            processes.append(subprocess.Popen([binary], cwd=root, env=env, stdout=log, stderr=log))
            base = f'http://127.0.0.1:{panel_port}/panel/api/'
            for _ in range(80):
                try:
                    request = urllib.request.Request(base + 'inbounds/list', headers={'Authorization': 'Bearer ' + token})
                    with opener.open(request, timeout=1) as response:
                        assert json.load(response)['success']
                    break
                except (OSError, AssertionError):
                    time.sleep(.25)
            else:
                raise RuntimeError('Panel did not become ready')
            with opener.open(f'http://127.0.0.1:{subscription_port}{clash_path}{result["subscriptionId"]}', timeout=10) as response:
                subscription = response.read().decode()
                assert subscription.count('type: vless') == 3
                assert subscription.count('type: hysteria2') == 1
                assert '2001:db8::10' in subscription
                assert '自建-直连' in subscription and '自建-住宅' in subscription
                assert 'kjxv' not in subscription.lower()
                assert 'reality-opts:' in subscription and 'MATCH,PROXY' in subscription
            before = database.execute('SELECT tag, settings, stream_settings FROM inbounds ORDER BY tag').fetchall()
            saved_template = database.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0]
            subprocess.run(command, cwd=root, env=env, check=True, stdout=subprocess.DEVNULL)
            assert before == database.execute('SELECT tag, settings, stream_settings FROM inbounds ORDER BY tag').fetchall()
            assert saved_template == database.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()[0]
            client_ports = []
            for index, (tag, settings_json, stream_json) in enumerate(inbounds):
                stream = json.loads(stream_json)
                client = json.loads(settings_json)['clients'][0]
                local_port = port()
                client_ports.append(local_port)
                client_config = {'log': {'loglevel': 'error'},
                    'inbounds': [{'listen':'127.0.0.1', 'port':local_port, 'protocol':'socks', 'settings':{'udp':False}}]}
                if tag != '默认HY2直连':
                    reality = stream.get('realitySettings', {})
                    client_config = {'log': {'loglevel': 'error'},
                        'inbounds': [{'listen':'127.0.0.1', 'port':local_port, 'protocol':'socks', 'settings':{'udp':False}}],
                        'outbounds': [{'protocol':'vless', 'settings':{'address':'::1' if index == 2 else '127.0.0.1', 'port':[direct_port, residential_port, result['ipv6Port'], result['hy2Port']][index],
                            'id':client['id'], 'flow':client['flow'], 'encryption':'none'},
                            'streamSettings': {'network':'tcp', 'security':'reality', 'realitySettings':{'serverName':'www.samsung.com',
                                'publicKey':reality['settings']['publicKey'], 'shortId':reality['shortIds'][0], 'fingerprint':'chrome'}}}]}
                if tag == '默认HY2直连':
                    client_config['outbounds'] = [{'protocol': 'hysteria',
                        'settings': {'version': 2, 'address': '127.0.0.1', 'port': result['hy2Port']},
                        'streamSettings': {'network': 'hysteria', 'security': 'tls',
                            'hysteriaSettings': {'version': 2, 'auth': client['auth']},
                            'tlsSettings': {'serverName': '127.0.0.1', 'alpn': ['h3'], 'disableSystemRoot': True,
                                'certificates': [{'certificateFile': str(work / 'cert.pem'), 'usage': 'verify'}]}}}]
                path = work / f'client-{index}.json'
                path.write_text(json.dumps(client_config))
                subprocess.run([core, 'run', '-test', '-c', str(path)], env=env, check=True, stdout=subprocess.DEVNULL)
                processes.append(subprocess.Popen([core, 'run', '-c', str(path)], env=env, stdout=log, stderr=log))
            time.sleep(1)
            assert b'preset-route-ok' in http_through_socks(client_ports[0], http_fixture.server_address[1])
            assert Socks.connections == 0, 'Direct node used residential SOCKS'
            assert b'preset-route-ok' in http_through_socks(client_ports[2], http_fixture.server_address[1])
            assert b'preset-route-ok' in http_through_socks(client_ports[3], http_fixture.server_address[1])
            assert Socks.connections == 0, 'IPv6 or HY2 used residential SOCKS'
            assert b'preset-route-ok' in http_through_socks(client_ports[1], http_fixture.server_address[1])
            assert Socks.connections == 1, 'Residential node bypassed residential SOCKS'
            socks.shutdown()
            socks.server_close()
            failed = False
            try:
                failed = b'preset-route-ok' not in http_through_socks(client_ports[1], http_fixture.server_address[1])
            except (OSError, ConnectionError):
                failed = True
            assert failed, 'Residential node silently fell back to direct'
            assert b'preset-route-ok' in http_through_socks(client_ports[0], http_fixture.server_address[1])
            print('PASS: actual REALITY/VLESS connections, authenticated residential SOCKS, failure isolation, IPv6 REALITY and certificate-verified HY2, four-node Clash subscription, repeated initialization.')
        except Exception:
            log.flush()
            print((work / 'runtime.log').read_text(errors='replace')[-6000:])
            raise
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            log.close()
            database.close()
            for server in servers:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    main()
