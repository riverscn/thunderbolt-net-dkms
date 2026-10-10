# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from privacy import findings

class ReleaseTests(unittest.TestCase):
    def test_private_data_rejected(self):
        # Construct fixtures so the scanner does not flag its own tests.
        for value in ('/Us' + 'ers/private-person/file',
                      '192.' + '168.23.42',
                      '10.' + '9.8.7',
                      'ghp_' + 'a' * 36,
                      '-----BEGIN ' + 'OPENSSH PRIVATE KEY-----'):
            self.assertTrue(findings(value), value)

    def test_documentation_addresses_accepted(self):
        self.assertFalse(findings('192.0.2.10 198.51.100.20 2001:db8:1::10'))

    def test_export_is_deterministic_and_allowlisted(self):
        spec = importlib.util.spec_from_file_location('make_dist', ROOT / 'scripts/make-dist.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            a = module.create(pathlib.Path(tmp) / 'a.tar.gz')
            b = module.create(pathlib.Path(tmp) / 'b.tar.gz')
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_release_has_install_opt_in_and_full_rollback(self):
        spec = importlib.util.spec_from_file_location('notes', ROOT / 'scripts/release-notes.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        text = module.render('example/thunderbolt-net-dkms')
        opening = text.split('## Changes in ', 1)[0]
        self.assertTrue(text.startswith('## Installation / upgrade\n'))
        for required in ('apt install build-essential dkms',
                         'linux-headers-$(uname -r)', 'proxmox-headers-$(uname -r)',
                         'sha256sum --check',
                         'options thunderbolt_net rx_segment=1 rx_segment_mtu=1500 rx_page_pool=1',
                         '/usr/lib/modprobe.d/thunderbolt-net.conf',
                         '/etc/modprobe.d/thunderbolt-net.conf',
                         'parameters/rx_page_pool', '--allow-downgrades',
                         'thunderbolt-net-dkms_0.2.0-1_all.deb'):
            self.assertIn(required, opening)
        self.assertIn('validation.md', opening)
        older = opening.split('For the older 0.1.1 baseline', 1)[1]
        self.assertIn('installation.md#return-to-the-011-baseline', older.split('\n\n', 1)[0])
        self.assertIn('page recycling by default', opening)
        self.assertNotIn('.conf.example', text)
        self.assertNotIn('riverscn/thunderbolt-net-dkms', text)

    def test_reload_instructions_keep_the_thunderbolt_core(self):
        # modprobe -r also unloads the unused core module and drops the link.
        spec = importlib.util.spec_from_file_location('notes', ROOT / 'scripts/release-notes.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        texts = {'release notes': module.render('example/thunderbolt-net-dkms'),
                 'hw-test.py': (ROOT / 'scripts/hw-test.py').read_text()}
        for path in [*ROOT.glob('*.md'), *(ROOT / 'docs').glob('*.md')]:
            texts[path.name] = path.read_text()
        for name, text in texts.items():
            self.assertNotIn('modprobe -r thunderbolt_net', text, name)
            self.assertNotIn("['modprobe', '-r'", text, name)
        # ifupdown hosts need the hotplug unit restarted around the reload.
        for name in ('release notes', 'installation.md'):
            self.assertIn('systemctl stop ifup@thunderbolt0.service', texts[name], name)
            self.assertIn('systemctl start ifup@thunderbolt0.service', texts[name], name)

    def test_release_version_is_consistent_after_version_bump(self):
        spec = importlib.util.spec_from_file_location('notes', ROOT / 'scripts/release-notes.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            fixture = pathlib.Path(tmp)
            (fixture / 'debian').mkdir()
            (fixture / 'VERSION').write_text('0.4.0\n')
            (fixture / 'debian/changelog').write_text(
                'thunderbolt-net (0.4.0-2) unstable; urgency=medium\n')
            (fixture / 'CHANGELOG.md').write_text(
                '# Changelog\n\n## 0.4.0\n\n- Next release fixture.\n'
                '\n## 0.3.0\n\n- Previous release fixture.\n')
            (fixture / 'packaging').mkdir()
            (fixture / 'packaging/thunderbolt-net.conf').write_text(
                '# comment\noptions thunderbolt_net rx_segment=0\n')
            module.ROOT = fixture
            text = module.render('example/thunderbolt-net-dkms')
        self.assertIn('**0.4.0:', text.split('## Changes in ', 1)[0])
        self.assertIn('/releases/download/v0.4.0/thunderbolt-net-dkms_0.4.0-2_all.deb', text)
        self.assertIn('/blob/v0.4.0/docs/installation.md', text)
        self.assertIn('Expect version `0.4.0`', text)
        self.assertIn('## Changes in v0.4.0\n\n- Next release fixture.', text)
        self.assertNotIn('0.3.0', text)
        self.assertNotIn('Previous release fixture.', text)
        self.assertIn('```conf\noptions thunderbolt_net rx_segment=0\n```', text)

    def test_rx_model_uses_the_tested_driver_functions(self):
        spec = importlib.util.spec_from_file_location(
            'extract', ROOT / 'tests/rx-lifecycle/extract.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            destination = pathlib.Path(tmp)
            self.assertEqual(len(module.prepare(destination)), 10)
            generated = (destination / 'core.inc').read_text()
            for body in module.fragments((ROOT / 'src/main.c').read_text()).values():
                self.assertIn(body, generated)

if __name__ == '__main__':
    unittest.main()
