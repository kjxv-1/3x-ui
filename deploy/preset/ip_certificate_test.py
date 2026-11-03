"""Exercise address selection and certificate rollback without touching host settings."""
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
BASH = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else shutil.which('bash')
OPENSSL = r'C:\Program Files\Git\usr\bin\openssl.exe' if os.name == 'nt' else shutil.which('openssl')


def helpers(name='install.sh'):
    text = (ROOT / name).read_text(encoding='utf-8')
    return text.split('# BEGIN shared IP certificate helpers', 1)[1].split('# END shared IP certificate helpers.', 1)[0].split('\n', 1)[1]


@unittest.skipUnless(BASH and OPENSSL, 'Bash and OpenSSL required')
class IPCertificateTest(unittest.TestCase):
    def run_shell(self, script, **variables):
        env = dict(os.environ)
        for key in ('XUI_SSL_IPV6', 'XUI_PRESET_IPV6_ADDRESS'):
            env.pop(key, None)
        env.update(variables)
        if os.name == 'nt':
            env['PATH'] = str(pathlib.Path(OPENSSL).parent) + os.pathsep + env['PATH']
        return subprocess.run([BASH, '-c', script], env=env, text=True, capture_output=True, encoding='utf-8')

    def test_installer_and_manager_share_the_same_logic(self):
        self.assertEqual(helpers(), helpers('x-ui.sh'))

    def test_no_ipv6_and_explicit_opt_out(self):
        for requested in ('auto', 'none'):
            result = self.run_shell(helpers() + '\nip() { return 0; }; ip_certificate_ipv6', XUI_SSL_IPV6=requested)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')

    def test_selects_only_a_ready_stable_public_address(self):
        addresses = '''2: eth0 inet6 fe80::1/64 scope link
2: eth0 inet6 fd00::1/64 scope global
2: eth0 inet6 2001:db8::1/64 scope global
2: eth0 inet6 2606:4700::1/64 scope global temporary
2: eth0 inet6 2606:4700::2/64 scope global tentative
2: eth0 inet6 2606:4700::3/64 scope global deprecated
2: eth0 inet6 2606:4700::4/64 scope global dadfailed
2: eth0 inet6 2606:4700::5/64 scope global
2: eth0 inet6 2606:4700::6/64 scope global
'''
        result = self.run_shell(helpers() + '\nip() { printf "%s" "$ADDRESSES"; }; ip_certificate_ipv6', ADDRESSES=addresses)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '2606:4700::5')

    def test_explicit_address_must_be_assigned_and_public(self):
        for address in ('fe80::1', 'fd00::1', '[2606:4700::1]', '2606:4700::1/64', '2606:4700::1'):
            result = self.run_shell(helpers() + '\nip() { return 0; }; ip_certificate_ipv6', XUI_SSL_IPV6=address)
            self.assertNotEqual(result.returncode, 0, address)

    def test_preset_override_and_certificate_override(self):
        for variables in ({'XUI_PRESET_IPV6_ADDRESS': '2606:4700::8'}, {'XUI_SSL_IPV6': '2606:4700::8'}):
            result = self.run_shell(helpers() + '''
ip() {
    [[ "$*" == '-o -6 addr show up scope global to 2606:4700::8/128' ]] || return 1
    echo '2: eth0 inet6 2606:4700::8/64 scope global'
}
ip_certificate_ipv6
''', **variables)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), '2606:4700::8')

    def make_certificate(self, directory, ipv6=False):
        directory.mkdir()
        san = 'IP:192.0.2.10' + (',IP:2606:4700::8' if ipv6 else '')
        subprocess.run([OPENSSL, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-keyout', str(directory / 'privkey.pem'), '-out', str(directory / 'fullchain.pem'),
                        '-subj', '/CN=fixture', '-addext', 'subjectAltName=' + san], check=True, capture_output=True)

    def test_install_accepts_correct_sans_and_rolls_back_invalid_files(self):
        with tempfile.TemporaryDirectory(prefix='codex-ip-cert-test-') as temporary:
            root = pathlib.Path(temporary)
            self.make_certificate(root / 'ipv4')
            self.make_certificate(root / 'dual', ipv6=True)
            shutil.copytree(root / 'dual', root / 'wrong-key')
            shutil.copyfile(root / 'ipv4' / 'privkey.pem', root / 'wrong-key' / 'privkey.pem')
            shutil.copytree(root / 'dual', root / 'corrupt')
            (root / 'corrupt' / 'fullchain.pem').write_text('not a certificate')
            acme = root / 'acme.sh'
            acme.write_text('''#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key-file) cp "$FIXTURE/privkey.pem" "$2"; shift 2 ;;
        --fullchain-file) cp "$FIXTURE/fullchain.pem" "$2"; shift 2 ;;
        *) shift ;;
    esac
done
# Real acme.sh can exit nonzero when only its reload hook fails.
exit 1
''', encoding='utf-8', newline='\n')
            acme.chmod(0o755)
            logic = helpers().replace('~/.acme.sh/acme.sh', '"' + acme.as_posix() + '"')
            for requested_v6, fixture, expected in [('', 'ipv4', 0), ('2606:4700::8', 'dual', 0), ('2606:4700::8', 'ipv4', 1), ('2606:4700::8', 'wrong-key', 1), ('2606:4700::8', 'corrupt', 1)]:
                dest = root / ('installed-' + fixture + ('-dual' if requested_v6 else '-v4'))
                shutil.copytree(root / 'dual', dest)
                before = {p.name: p.read_bytes() for p in dest.iterdir()}
                result = self.run_shell(logic + '\ninstall_ip_certificate_files 192.0.2.10 "$IPV6" "$DEST" true',
                                        FIXTURE=(root / fixture).as_posix(), IPV6=requested_v6, DEST=dest.as_posix())
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected:
                    self.assertEqual({p.name: p.read_bytes() for p in dest.iterdir()}, before)
                else:
                    self.assertEqual((dest / 'fullchain.pem').read_bytes(), (root / fixture / 'fullchain.pem').read_bytes())

    def test_failed_acme_issue_preserves_existing_files(self):
        # Run the real issuer function with only external operations stubbed.
        with tempfile.TemporaryDirectory(prefix='codex-ip-issue-test-') as temporary:
            root = pathlib.Path(temporary)
            self.make_certificate(root / 'installed', ipv6=True)
            before = {p.name: p.read_bytes() for p in (root / 'installed').iterdir()}
            acme = root / 'acme.sh'
            acme.write_text('#!/usr/bin/env bash\n[[ " $* " != *" --issue "* ]]\n', encoding='utf-8', newline='\n')
            acme.chmod(0o755)
            source = (ROOT / 'install.sh').read_text(encoding='utf-8')
            issuer = re.search(r'^setup_ip_certificate\(\) \{.*?^\}', source, re.M | re.S)[0]
            issuer = issuer.replace('/root/cert/ip', (root / 'installed').as_posix()).replace('~/.acme.sh/acme.sh', '"' + acme.as_posix() + '"')
            result = self.run_shell(helpers() + issuer + '''
is_ipv4() { return 0; }
is_port_in_use() { return 1; }
prompt_or_default() { printf -v "$1" '%s' 80; }
setup_ip_certificate 192.0.2.10 2606:4700::8
''')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual({p.name: p.read_bytes() for p in (root / 'installed').iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
