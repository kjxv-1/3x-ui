"""Verify fixed-version publication, failed upload recovery and preservation of old tags."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('publish.sh').resolve()
TAG = 'v3.10.2-preset-sort'
SHA = 'a' * 40
PACKAGES = [f'x-ui-linux-{a}.tar.gz' for a in ('amd64','arm64','armv7','armv6','armv5','386','s390x')] + ['x-ui-windows-amd64.zip']
FAKE_GH = r'''
import hashlib,json,os,pathlib,shutil,sys
root=pathlib.Path(os.environ['PRESET_TEST_STATE'])
state_path=root/'state.json'
s=json.loads(state_path.read_text())
args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f: f.write(json.dumps(args)+'\n')
def option(key): return args[args.index(key)+1]
def save(): state_path.write_text(json.dumps(s))
if args[0]=='api':
    endpoint=next(a for a in args if a.startswith('repos/'))
    assert endpoint.startswith('repos/kjxv-1/3x-ui/')
    method=option('--method') if '--method' in args else 'GET'
    if endpoint.endswith('/git/ref/heads/codex/v3.10.2-preset-sort'):
        print(os.environ['GITHUB_SHA'])
    elif endpoint.endswith('/git/ref/tags/v3.10.2-preset-sort'):
        if s['ref'] is None: sys.exit(1)
        print(s['ref'])
    elif endpoint.endswith('/git/refs') and method=='POST':
        assert 'ref=refs/tags/v3.10.2-preset-sort' in args
        s['ref']=next(a[4:] for a in args if a.startswith('sha=')); save()
    elif endpoint.endswith('/git/refs/tags/v3.10.2-preset-sort') and method=='PATCH':
        assert s['release']['draft']
        s['ref']=next(a[4:] for a in args if a.startswith('sha=')); save()
    elif endpoint.endswith('/releases'):
        assets=[]
        for p in (root/'assets').iterdir():
            assets.append({'name':p.name,'size':p.stat().st_size,'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()})
        print(json.dumps([[dict(s['release'],assets=assets)] if s['release'] else []]))
    else: raise RuntimeError(args)
elif args[:2]==['release','create']:
    assert args[2]=='v3.10.2-preset-sort' and '--draft' in args
    s['release']={'draft':True,'tag_name':'v3.10.2-preset-sort'}; save()
elif args[:2]==['release','upload']:
    assert args[2]=='v3.10.2-preset-sort' and s['release']['draft']
    for i,name in enumerate(args[3:args.index('--repo')]):
        p=pathlib.Path(name); shutil.copyfile(p,root/'assets'/p.name)
        if i==0 and os.environ.get('PRESET_TEST_FAIL_UPLOAD'):
            sys.exit(1)
elif args[:2]==['release','edit']:
    assert args[2]=='v3.10.2-preset-sort' and '--latest=false' in args
    s['release']['draft']=False; save()
else: raise RuntimeError(args)
'''


class PublishTests(unittest.TestCase):
    def run_case(self, mode='new'):
        bash=r'C:/Program Files/Git/bin/bash.exe' if os.name=='nt' else shutil.which('bash')
        with tempfile.TemporaryDirectory(prefix='xui-publish-') as temporary:
            root=Path(temporary); stub=root/'stub'; stub.mkdir()
            state=root/'state'; state.mkdir(); (state/'assets').mkdir()
            initial={'ref':None,'release':None,'oldTags':{'v3.10.2-preset-ipv6-hy2':'b'*40},'latest':'v3.6.0'}
            if mode=='wrong-tag': initial['ref']='c'*40
            if mode in ('published','draft','repaired-draft','published-other'):
                initial['ref']='c'*40 if mode in ('repaired-draft','published-other') else SHA
                initial['release']={'draft':mode in ('draft','repaired-draft'),'tag_name':TAG}
            (state/'state.json').write_text(json.dumps(initial))
            packages=root/'packages'; packages.mkdir()
            for name in PACKAGES: (packages/name).write_text('package '+name)
            if mode=='missing': (packages/PACKAGES[0]).unlink()
            (root/'internal/config').mkdir(parents=True)
            (root/'internal/config/version').write_text(TAG[1:])
            (root/'deploy/preset').mkdir(parents=True)
            for name in ('install.sh','x-ui.sh','update.sh','deploy/preset/README.md','deploy/preset/release-notes.md'):
                (root/name).write_text('new '+name)
            fake=root/'gh.py'; fake.write_text(FAKE_GH,encoding='utf-8')
            for name,body in {
                'gh':'exec "$PRESET_TEST_PYTHON" "$PRESET_TEST_GH" "$@"',
                'git':'printf "%s\\n" "$GITHUB_SHA"',
                'python3':'exec "$PRESET_TEST_PYTHON" "$@"',
            }.items():
                (stub/name).write_text('#!/usr/bin/env bash\n'+body+'\n'); (stub/name).chmod(0o755)
            env=dict(os.environ,GITHUB_REPOSITORY='kjxv-1/3x-ui',GITHUB_SHA=SHA,PRESET_RELEASE_TAG=TAG,
                     PRESET_TEST_PYTHON=sys.executable,PRESET_TEST_GH=fake.as_posix(),PRESET_TEST_STATE=state.as_posix(),
                     PRESET_TEST_SCRIPT=SCRIPT.as_posix(),PRESET_TEST_STUB=stub.as_posix())
            if mode=='wrong-repo': env['GITHUB_REPOSITORY']='kjxv/3x-ui'
            if mode=='fail-upload': env['PRESET_TEST_FAIL_UPLOAD']='1'
            result=subprocess.run([bash,'-c','if command -v cygpath >/dev/null; then PRESET_TEST_STUB=$(cygpath -u "$PRESET_TEST_STUB"); fi; export PATH="$PRESET_TEST_STUB:$PATH"; exec bash "$PRESET_TEST_SCRIPT" packages'],cwd=root,env=env,
                                  text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            final=json.loads((state/'state.json').read_text())
            calls=[json.loads(s) for s in (state/'calls.jsonl').read_text().splitlines()] if (state/'calls.jsonl').exists() else []
            self.assertEqual(final['oldTags'],initial['oldTags'])
            self.assertEqual(final['latest'],initial['latest'])
            if mode != 'repaired-draft':
                self.assertFalse(any('--method' in c and 'PATCH' in c for c in calls))
            if mode in ('new','draft','repaired-draft'):
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(final['ref'],SHA)
                self.assertFalse(final['release']['draft'])
                self.assertEqual(len(list((state/'assets').iterdir())),21)
            elif mode=='published':
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(initial,final)
                self.assertFalse(any(c[:2]==['release','upload'] for c in calls))
            elif mode=='fail-upload':
                self.assertNotEqual(result.returncode,0)
                self.assertTrue(final['release']['draft'])
            else:
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(initial,final)
            return result

    def test_new_fixed_release_has_all_packages_checksums_and_commands(self): self.run_case()
    def test_existing_draft_can_resume(self): self.run_case('draft')
    def test_repaired_build_can_replace_its_own_unpublished_draft(self): self.run_case('repaired-draft')
    def test_failed_upload_remains_unpublished(self): self.run_case('fail-upload')
    def test_published_version_is_never_overwritten(self): self.run_case('published')
    def test_existing_different_tag_is_never_moved(self): self.run_case('wrong-tag')
    def test_published_tag_never_moves_to_a_different_commit(self): self.run_case('published-other')
    def test_missing_architecture_is_rejected(self): self.run_case('missing')
    def test_wrong_account_is_rejected(self): self.run_case('wrong-repo')


if __name__=='__main__': unittest.main()
