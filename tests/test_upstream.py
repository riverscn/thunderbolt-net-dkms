# SPDX-License-Identifier: GPL-2.0-only
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import subprocess
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('upstream_tool', ROOT / 'scripts/upstream.py')
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)


class UpstreamTests(unittest.TestCase):
    def fixture(self, root):
        for name in ('upstream', 'src'):
            (root / name).mkdir()
        metadata = {'tag': 'v7.2.9', 'commit': 'a' * 40, 'repository': 'gregkh/linux',
                    'files': {}, 'urls': {}}
        for name in upstream.FILES:
            (root / 'upstream' / name).write_text('first\nbase\nlast\n')
            (root / 'src' / name).write_text('first\nlocal\nlast\n')
            metadata['files'][name] = hashlib.sha256((root / 'upstream' / name).read_bytes()).hexdigest()
            metadata['urls'][name] = f'https://raw.githubusercontent.com/gregkh/linux/{"a" * 40}/drivers/net/thunderbolt/{name}'
        (root / 'upstream/provenance.json').write_text(json.dumps(metadata))
        (root / 'upstream/local.patch').write_text(upstream.delta(root))

    def test_replay_detects_unrecorded_local_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.fixture(root)
            upstream.verify(root)
            (root / 'src/main.c').write_text('unreviewed\n')
            with self.assertRaisesRegex(ValueError, 'stale'):
                upstream.verify(root)

    def test_provenance_rejects_modified_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.fixture(root)
            (root / 'upstream/main.c').write_text('changed\n')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                upstream.verify(root)

    def original_mail(self, directory):
        directory.mkdir()
        subprocess.run(['git', 'init', '-q', str(directory)], check=True)
        def run(*args):
            return subprocess.check_output(['git', '-c', 'user.name=Original Author',
                '-c', 'user.email=author@example.org', *args], cwd=directory)
        path = directory / 'drivers/net/thunderbolt'
        path.mkdir(parents=True)
        for name in upstream.FILES:
            (path / name).write_text('first\nbase\nlast\n')
        run('add', '.')
        run('commit', '-qm', 'Base')
        for name in upstream.FILES:
            (path / name).write_text('first\nremote\nlast\n')
        (directory / 'include').mkdir()
        (directory / 'include/core.h').write_text('controller dependency\n')
        run('add', '.')
        run('commit', '-qm', 'Original fix\n\nReason for this change.\n\nSigned-off-by: Original Author <author@example.org>',
            '--date=2026-01-02T03:04:05+02:00')
        sha = run('rev-parse', 'HEAD').decode().strip()
        return sha, run('format-patch', '-1', '--stdout'), run('show', '-s', '--format=%an%n%ae%n%aI%n%B').decode()

    def test_import_preserves_authorship_and_conflicts_without_overwriting_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            sha, mail, original = self.original_mail(tmp / 'linux')
            root = tmp / 'repo'
            root.mkdir()
            self.fixture(root)
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            out = tmp / 'candidate'
            def fetch(url):
                return mail if url.endswith('.patch') else b'first\nremote\nlast\n'
            with patch.object(upstream, 'resolve_tag', return_value='b' * 40), \
                 patch.object(upstream, 'fetch', side_effect=fetch):
                self.assertTrue(upstream.prepare('v7.2.10', out, root, [sha]))
            self.assertEqual(json.loads((out / 'review.json').read_text())['conflicts'], list(upstream.FILES))
            self.assertIn('<<<<<<<', (out / 'src/main.c').read_text())
            imported = upstream.git(out, 'show', '-s', '--format=%an%n%ae%n%aI%n%B', capture_output=True).stdout.decode()
            self.assertTrue(imported.startswith(original.rstrip()))
            self.assertIn('Upstream-commit: ' + sha, imported)
            self.assertFalse((out / 'include/core.h').exists())
            self.assertEqual((out / f'upstream/commits/{sha}.patch').read_bytes(), mail)
            self.assertIn(b'include/core.h', mail)
            for p, data in before.items():
                self.assertEqual(data, p.read_bytes())
            with self.assertRaisesRegex(ValueError, 'must not exist'):
                upstream.prepare('v7.2.10', out, root, [sha])

    def test_missing_commit_cannot_be_replaced_by_endpoint_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / 'repo'
            root.mkdir()
            self.fixture(root)
            out = pathlib.Path(tmp) / 'candidate'
            with patch.object(upstream, 'resolve_tag', return_value='b' * 40), \
                 patch.object(upstream, 'fetch', return_value=b'first\nremote\nlast\n'):
                with self.assertRaisesRegex(ValueError, 'does not reproduce target baseline'):
                    upstream.prepare('v7.2.10', out, root)
            self.assertEqual((out / 'upstream/main.c').read_text(), 'first\nbase\nlast\n')

    def test_original_archive_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            sha, mail, _ = self.original_mail(tmp / 'linux')
            root = tmp / 'repo'
            root.mkdir()
            self.fixture(root)
            p = root / f'upstream/commits/{sha}.patch'
            p.parent.mkdir()
            p.write_bytes(mail)
            manifest = root / 'upstream/provenance.json'
            metadata = json.loads(manifest.read_text())
            metadata['imports'] = [{'commit': sha, 'path': str(p.relative_to(root)),
                                    'sha256': hashlib.sha256(mail).hexdigest()}]
            manifest.write_text(json.dumps(metadata))
            upstream.verify(root)
            p.write_bytes(mail + b'altered\n')
            with self.assertRaisesRegex(ValueError, 'original patch checksum mismatch'):
                upstream.verify(root)

    def test_kbuild_probe_requires_declaration_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'include/linux').mkdir(parents=True)
            for declaration, exported in ((False, False), (True, False), (False, True), (True, True)):
                (root / 'include/linux/thunderbolt.h').write_text(
                    'int tb_ring_throttling(struct tb_ring *, unsigned int);\n' if declaration else '')
                (root / 'Module.symvers').write_text(
                    '0x0\ttb_ring_throttling\tdrivers/thunderbolt/thunderbolt\tEXPORT_SYMBOL_GPL\n' if exported else '')
                source = f'include {ROOT}/src/Makefile\nall:\n\t@echo $(ccflags-y)\n'
                result = subprocess.run(['make', '-s', '-f', '-', f'srctree={root}',
                    f'objtree={root}', f'src={ROOT}/src', 'all'], input=source,
                    capture_output=True, text=True, check=True, timeout=10)
                self.assertEqual('-DTBNET_HAVE_RING_THROTTLING' in result.stdout,
                                 declaration and exported)

    def test_rejects_rc_or_untrusted_tag_before_network(self):
        with patch.object(upstream, 'fetch') as fetch:
            for tag in ('main', 'v7.3-rc6', '../main', 'v7.2;echo'):
                with self.assertRaises(ValueError):
                    upstream.resolve_tag(tag)
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
