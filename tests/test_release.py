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

if __name__ == '__main__':
    unittest.main()
