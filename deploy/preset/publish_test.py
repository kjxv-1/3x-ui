"""Exercise stable-release publication and partial-upload rollback without GitHub writes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('publish.sh').resolve()
TAG = 'v3.10.2-preset-ipv6-hy2'
SHA = 'a' * 40
OLD_SHA = 'b' * 40
PACKAGES = [f'x-ui-linux-{arch}.tar.gz' for arch in ('amd64', 'arm64', 'armv7', 'armv6', 'armv5', '386', 's390x')] + ['x-ui-windows-amd64.zip']
FAKE_GH = r"""
import json,os,pathlib,shutil,sys
state_dir=pathlib.Path(os.environ['PRESET_TEST_STATE'])
state_path=state_dir/'state.json'
state=json.loads(state_path.read_text(encoding='utf-8'))
args=sys.argv[1:]
with (state_dir/'calls.jsonl').open('a',encoding='utf-8') as log:
    log.write(json.dumps(args)+'\n')
def option(name):
    return args[args.index(name)+1]
def save():
    state_path.write_text(json.dumps(state),encoding='utf-8')
if args[0]=='api':
    endpoint=next(a for a in args[1:] if a.startswith('repos/'))
    assert endpoint.startswith('repos/kjxv-1/3x-ui/')
    method=option('--method') if '--method' in args else 'GET'
    if method=='GET':
        if endpoint.endswith('/git/ref/heads/codex/v3.10.2-preset-ipv6-hy2'):
            print(os.environ['GITHUB_SHA'])
        elif endpoint.endswith('/git/ref/tags/v3.10.2-preset-ipv6-hy2'):
            print(state['ref'])
        elif endpoint.endswith('/releases/tags/v3.10.2-preset-ipv6-hy2'):
            print(json.dumps(state['release']))
        else:
            raise RuntimeError(endpoint)
    elif method=='PATCH':
        if endpoint.endswith('/git/refs/tags/v3.10.2-preset-ipv6-hy2'):
            state['ref']=option('-f').split('=',1)[1]
        elif endpoint.endswith('/releases/123'):
            payload=json.loads(pathlib.Path(option('--input')).read_text(encoding='utf-8'))
            state['release'].update(payload)
        else:
            raise RuntimeError(endpoint)
        save()
        print('{}')
    else:
        raise RuntimeError(method)
elif args[:2]==['release','download']:
    assert args[2]=='v3.10.2-preset-ipv6-hy2'
    dest=pathlib.Path(option('--dir'))
    for source in (state_dir/'assets').iterdir(): shutil.copyfile(source,dest/source.name)
elif args[:2]==['release','edit']:
    assert args[2]=='v3.10.2-preset-ipv6-hy2' and '--draft=true' in args
    state['release']['draft']=True
    save()
elif args[:2]==['release','upload']:
    assert args[2]=='v3.10.2-preset-ipv6-hy2' and '--clobber' in args
    files=args[3:args.index('--repo')]
    for i,value in enumerate(files):
        p=pathlib.Path(value)
        shutil.copyfile(p,state_dir/'assets'/p.name)
        if i==0 and os.environ.get('PRESET_TEST_FAIL_UPLOAD')=='1' and not state.get('failed_once'):
            state['failed_once']=True
            save()
            sys.exit(1)
    save()
else:
    raise RuntimeError(args)
"""


class PublishTests(unittest.TestCase):
    def run_case(self, fail_upload=False, missing_package=False, repository='kjxv-1/3x-ui'):
        bash = r'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')
        self.assertTrue(bash)
        with tempfile.TemporaryDirectory(prefix='preset-publication-test-') as temporary:
            root=Path(temporary)
            stub=root/'stub'; stub.mkdir()
            state_dir=root/'state'; state_dir.mkdir()
            old_assets=state_dir/'assets'; old_assets.mkdir()
            packages=root/'packages'; packages.mkdir()
            (root/'internal/config').mkdir(parents=True)
            (root/'internal/config/version').write_text(TAG[1:],encoding='utf-8')
            (root/'deploy/preset').mkdir(parents=True)
            (root/'deploy/preset/release-notes.md').write_text('Fixed deployment entry.\n',encoding='utf-8')
            (root/'deploy/preset/README.md').write_text('new readme',encoding='utf-8')
            (root/'install.sh').write_text('new installer',encoding='utf-8')
            (root/'x-ui.sh').write_text('new menu',encoding='utf-8')
            names=PACKAGES+[name+'.sha256' for name in PACKAGES]+['checksums.txt','install.sh','x-ui.sh','README.md']
            for name in names:
                (old_assets/name).write_bytes(('old '+name).encode())
            for name in PACKAGES:
                (packages/name).write_bytes(('new '+name).encode())
            if missing_package: (packages/PACKAGES[0]).unlink()
            prior={p.name:p.read_bytes() for p in old_assets.iterdir()}
            meta={'id':123,'tag_name':TAG,'name':'previous title','body':'previous notes','draft':False,'prerelease':False,'immutable':False,
                  'assets':[{'name':name,'size':len(prior[name]),'digest':'sha256:'+hashlib.sha256(prior[name]).hexdigest()} for name in names]}
            original={'ref':OLD_SHA,'release':meta,'latest':'v3.6.0'}
            (state_dir/'state.json').write_text(json.dumps(original),encoding='utf-8')
            fake=root/'gh_stub.py'; fake.write_text(FAKE_GH,encoding='utf-8')
            (stub/'gh').write_bytes(b'#!/usr/bin/env bash\nexec "$PRESET_TEST_PYTHON" "$PRESET_TEST_GH" "$@"\n')
            (stub/'git').write_bytes(b'#!/usr/bin/env bash\nprintf "%s\\n" "$GITHUB_SHA"\n')
            (stub/'gh').chmod(0o755); (stub/'git').chmod(0o755)
            env=dict(os.environ,GITHUB_REPOSITORY=repository,GITHUB_SHA=SHA,PRESET_RELEASE_TAG=TAG,
                     PRESET_TEST_STATE=state_dir.as_posix(),PRESET_TEST_GH=fake.as_posix(),PRESET_TEST_PYTHON=Path(sys.executable).as_posix(),
                     PRESET_TEST_FAIL_UPLOAD='1' if fail_upload else '0',RUNNER_TEMP=root.as_posix(),
                     PRESET_TEST_STUB=stub.as_posix(),PRESET_TEST_SCRIPT=SCRIPT.as_posix(),
                     PATH=str(stub)+os.pathsep+os.environ['PATH'])
            # Linux CI uses python3; Git Bash on Windows also resolves this fixture wrapper.
            (stub/'python3').write_bytes(b'#!/usr/bin/env bash\nexec "$PRESET_TEST_PYTHON" "$@"\n')
            (stub/'python3').chmod(0o755)
            result=subprocess.run([bash,'-c','if command -v cygpath >/dev/null; then PRESET_TEST_STUB=$(cygpath -u "$PRESET_TEST_STUB"); fi; export PATH="$PRESET_TEST_STUB:$PATH"; exec bash "$PRESET_TEST_SCRIPT" packages'],cwd=root,env=env,text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            state=json.loads((state_dir/'state.json').read_text(encoding='utf-8'))
            actual={p.name:p.read_bytes() for p in old_assets.iterdir()}
            calls=[json.loads(line) for line in (state_dir/'calls.jsonl').read_text(encoding='utf-8').splitlines()] if (state_dir/'calls.jsonl').exists() else []
            self.assertFalse(list(root.glob('preset-release.*')), 'Publisher left its temporary rollback files')
            return result,state,actual,original,prior,calls

    def test_updates_same_release_and_tag_without_changing_original_tutorial(self):
        result,state,actual,original,prior,calls=self.run_case()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(state['ref'],SHA)
        self.assertEqual(state['release']['id'],original['release']['id'])
        self.assertFalse(state['release']['draft'])
        self.assertEqual(state['latest'],'v3.6.0')
        self.assertIn(SHA,state['release']['body'])
        self.assertEqual(set(actual),set(prior))
        for name in PACKAGES: self.assertEqual(actual[name],('new '+name).encode())
        self.assertIn(b'x-ui-windows-amd64.zip',actual['checksums.txt'])

    def test_partial_upload_restores_previous_release_packages_and_tag(self):
        result,state,actual,original,prior,calls=self.run_case(fail_upload=True)
        self.assertNotEqual(result.returncode,0)
        self.assertTrue(state.get('failed_once'),result.stderr)
        self.assertIn('restoring the previous tag',result.stderr)
        self.assertEqual(state['ref'],OLD_SHA)
        self.assertEqual(state['release']['name'],original['release']['name'])
        self.assertEqual(state['release']['body'],original['release']['body'])
        self.assertFalse(state['release']['draft'])
        self.assertEqual(actual,prior)
        self.assertEqual(state['latest'],'v3.6.0')

    def test_incomplete_packages_do_not_change_existing_release(self):
        result,state,actual,original,prior,calls=self.run_case(missing_package=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Exactly eight platform packages are required',result.stderr)
        self.assertEqual(state,original)
        self.assertEqual(actual,prior)
        self.assertFalse(any('--method' in call for call in calls))

    def test_refuses_another_repository_before_any_remote_operation(self):
        result,state,actual,original,prior,calls=self.run_case(repository='other/project')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(calls)
        self.assertEqual(state,original)


if __name__=='__main__':
    unittest.main()
