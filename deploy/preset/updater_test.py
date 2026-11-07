"""Run the delivered updater with fake services/downloads and real SQLite backups."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('update.sh').resolve()
VERSION = '3.10.2-preset-sort'
FAKE = r'''
import json, os, pathlib, shutil, sqlite3, sys
root = pathlib.Path(os.environ['UPDATE_TEST_ROOT'])
command = sys.argv[1]
args = sys.argv[2:]
with (root/'calls.jsonl').open('a') as f: f.write(json.dumps([command]+args)+'\n')
if command == 'curl':
    url = next(a for a in args if a.startswith(('https://','http://')))
    if url.startswith('https://github.com/'):
        out = pathlib.Path(args[args.index('-o')+1])
        if url.endswith('.sha256'):
            import hashlib
            digest = hashlib.sha256((root/'bundle.tar.gz').read_bytes()).hexdigest()
            if os.environ.get('UPDATE_TEST_BAD_CHECKSUM'): digest = '0'*64
            out.write_text(digest+'  x-ui-linux-amd64.tar.gz\n')
        else: shutil.copyfile(root/'bundle.tar.gz',out)
    else: print('200',end='')
elif command == 'systemctl':
    state_path = root/'service.json'
    state = json.loads(state_path.read_text())
    if args[0] == 'cat': pass
    elif args[0] == 'show': print(os.environ.get('UPDATE_TEST_SERVICE_ENV',''))
    elif args[0] == 'is-active': sys.exit(0 if state['running'] else 3)
    elif args[0] == 'stop': state['running']=False
    elif args[0] == 'start':
        new = '3.10.2-preset-sort' in (root/'usr/local/x-ui/x-ui').read_text()
        if new and os.environ.get('UPDATE_TEST_FAIL_START'):
            with sqlite3.connect(root/'etc/x-ui/x-ui.db') as c:
                c.execute("UPDATE inbounds SET remark='simulate failed migration'")
            (root/'etc/x-ui/x-ui.db-wal').write_text('incomplete wal')
            sys.exit(1)
        state['running']=True
    else: raise RuntimeError(args)
    state_path.write_text(json.dumps(state))
else: raise RuntimeError(command)
'''


class UpdaterTests(unittest.TestCase):
    def run_case(self, mode='success'):
        bash = r'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
        with tempfile.TemporaryDirectory(prefix='xui-updater-') as temporary:
            root = Path(temporary)
            app = root/'usr/local/x-ui'
            app.mkdir(parents=True)
            db = root/'etc/x-ui'
            db.mkdir(parents=True)
            menu = root/'usr/bin/x-ui'
            menu.parent.mkdir(parents=True)
            old = b'#!/usr/bin/env bash\necho 3.10.2-preset-ipv6-hy2\n'
            (app/'x-ui').write_bytes(old)
            (app/'x-ui').chmod(0o755)
            (app/'x-ui.sh').write_bytes(b'old-local-menu')
            (app/'bin').mkdir()
            (app/'bin/xray').write_bytes(b'preserved custom core')
            menu.write_bytes(b'old-system-menu')
            with sqlite3.connect(db/'x-ui.db') as c:
                c.execute('CREATE TABLE settings (key TEXT, value TEXT)')
                c.execute('CREATE TABLE inbounds (id INT, remark TEXT)')
                c.execute("INSERT INTO inbounds VALUES (1, 'user residential node')")
                c.executemany('INSERT INTO settings VALUES (?,?)', [
                    ('webPort','23353'),('webBasePath','/preserved/'),
                    ('subURI','https://[2001:db8::1]/preserved'),('remarkTemplate','kept'),
                    ('webCertFile','/root/cert/original.pem'),('webKeyFile','/root/cert/original.key'),
                ])
            c.close()
            before = (db/'x-ui.db').read_bytes()
            (db/'install-result.env').write_text('original credentials retained')
            (root/'service.json').write_text('{"running":true}')
            version = '9.9.9' if mode == 'wrong-version' else VERSION
            with tarfile.open(root/'bundle.tar.gz','w:gz') as archive:
                for name, payload in {
                    'x-ui/x-ui':f'#!/usr/bin/env bash\necho {version}\n'.encode(),
                    'x-ui/x-ui.sh':b'#!/usr/bin/env bash\n# new menu\n',
                    'x-ui/bin/xray':b'must not replace custom core',
                }.items():
                    member=tarfile.TarInfo(name); member.size=len(payload); member.mode=0o755
                    archive.addfile(member,io.BytesIO(payload))
            stub=root/'stub'; stub.mkdir()
            (root/'fake.py').write_text(FAKE,encoding='utf-8')
            for command in ('curl','systemctl'):
                (stub/command).write_text('#!/usr/bin/env bash\nexec "$UPDATE_TEST_PYTHON" "$UPDATE_TEST_FAKE" '+command+' "$@"\n',encoding='utf-8')
                (stub/command).chmod(0o755)
            (stub/'python3').write_text('#!/usr/bin/env bash\nexec "$UPDATE_TEST_PYTHON" "$@"\n')
            (stub/'python3').chmod(0o755)
            if os.name == 'nt':
                # Git Bash omits flock; service/download logic is still tested.
                (stub/'flock').write_text('#!/usr/bin/env bash\nexit 0\n')
                (stub/'flock').chmod(0o755)
            env=dict(os.environ, XUI_UPDATE_ROOT=root.as_posix(), UPDATE_TEST_ROOT=root.as_posix(),
                     UPDATE_TEST_PYTHON=sys.executable, UPDATE_TEST_FAKE=(root/'fake.py').as_posix(),
                     UPDATE_TEST_SCRIPT=SCRIPT.as_posix(), UPDATE_TEST_STUB=stub.as_posix())
            env.pop('XUI_DB_FOLDER',None); env.pop('XUI_DB_TYPE',None)
            if os.name == 'nt': env['TAR_OPTIONS']='--force-local'
            if mode == 'bad-checksum': env['UPDATE_TEST_BAD_CHECKSUM']='1'
            if mode == 'fail-start': env['UPDATE_TEST_FAIL_START']='1'
            if mode == 'postgres': env['UPDATE_TEST_SERVICE_ENV']='XUI_DB_TYPE=postgres'
            result=subprocess.run([bash,'-c','if command -v cygpath >/dev/null; then UPDATE_TEST_STUB=$(cygpath -u "$UPDATE_TEST_STUB"); fi; export PATH="$UPDATE_TEST_STUB:$PATH"; exec bash "$UPDATE_TEST_SCRIPT"'],env=env,cwd=root,
                                  text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertTrue((root/'calls.jsonl').exists(),result.stderr)
            calls=[json.loads(line) for line in (root/'calls.jsonl').read_text().splitlines()]
            self.assertEqual((db/'x-ui.db').read_bytes(),before,result.stderr)
            self.assertEqual((db/'install-result.env').read_text(),'original credentials retained')
            self.assertEqual((app/'bin/xray').read_bytes(),b'preserved custom core')
            self.assertTrue(json.loads((root/'service.json').read_text())['running'])
            backups=list((root/'var/backups/3x-ui').glob('2*'))
            if mode == 'success':
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn(VERSION,(app/'x-ui').read_text())
                self.assertEqual((backups[0]/'database/x-ui.db').read_bytes(),before)
                self.assertEqual((backups[0]/'x-ui').read_bytes(),old)
            else:
                self.assertNotEqual(result.returncode,0,result.stdout)
                self.assertEqual((app/'x-ui').read_bytes(),old)
                self.assertEqual(menu.read_bytes(),b'old-system-menu')
                if mode != 'fail-start':
                    self.assertFalse(any(c[:2]==['systemctl','stop'] for c in calls))
                else:
                    self.assertFalse((db/'x-ui.db-wal').exists())
                    self.assertIn('已恢复原程序',result.stderr)
            return result

    def test_success_preserves_database_credentials_certificates_and_custom_core(self):
        self.run_case()

    def test_corrupt_download_cannot_stop_or_change_the_panel(self):
        self.run_case('bad-checksum')

    def test_wrong_packaged_version_is_rejected_before_downtime(self):
        self.run_case('wrong-version')

    def test_failed_start_restores_program_database_wal_and_service(self):
        self.run_case('fail-start')

    def test_nonstandard_database_is_rejected_before_downtime(self):
        self.run_case('postgres')


if __name__ == '__main__':
    unittest.main()
