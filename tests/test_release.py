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
                         'sha256sum --check', 'rx_page_pool=1',
                         'thunderbolt-net-page-pool.conf.example',
                         'parameters/rx_page_pool', '--allow-downgrades',
                         'thunderbolt-net-dkms_0.1.1-1_all.deb'):
            self.assertIn(required, opening)
        self.assertIn('validation.md', opening)
        self.assertIn('disabled by default', opening)
        self.assertNotIn('riverscn/thunderbolt-net-dkms', text)

    def test_release_version_is_consistent_after_version_bump(self):
        spec = importlib.util.spec_from_file_location('notes', ROOT / 'scripts/release-notes.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            fixture = pathlib.Path(tmp)
            (fixture / 'debian').mkdir()
            (fixture / 'VERSION').write_text('0.3.0\n')
            (fixture / 'debian/changelog').write_text(
                'thunderbolt-net (0.3.0-2) unstable; urgency=medium\n')
            (fixture / 'CHANGELOG.md').write_text(
                '# Changelog\n\n## 0.3.0\n\n- Next release fixture.\n'
                '\n## 0.2.0\n\n- Previous release fixture.\n')
            module.ROOT = fixture
            text = module.render('example/thunderbolt-net-dkms')
        self.assertIn('**0.3.0:', text.split('## Changes in ', 1)[0])
        self.assertIn('/releases/download/v0.3.0/thunderbolt-net-dkms_0.3.0-2_all.deb', text)
        self.assertIn('/blob/v0.3.0/docs/installation.md', text)
        self.assertIn('Expect version `0.3.0`', text)
        self.assertIn('## Changes in v0.3.0\n\n- Next release fixture.', text)
        self.assertNotIn('0.2.0', text)
        self.assertNotIn('Previous release fixture.', text)

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
