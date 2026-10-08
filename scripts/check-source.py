#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import hashlib
import json
import os
import pathlib
import re
import sys
from privacy import findings

ROOT = pathlib.Path(__file__).resolve().parent.parent

def check(root=ROOT):
    paths = (root / 'release-files.txt').read_text().splitlines()
    errors = []
    if paths != sorted(set(paths)):
        errors.append('release-files.txt must be sorted and unique')
    private_terms = []
    if os.environ.get('PRIVATE_SCAN_TERMS_FILE'):
        private_terms = pathlib.Path(os.environ['PRIVATE_SCAN_TERMS_FILE']).read_text().splitlines()
    for name in paths:
        p = root / name
        if (name.startswith('/') or '..' in pathlib.PurePosixPath(name).parts or
                p.is_symlink() or not p.resolve().is_relative_to(root.resolve())):
            errors.append(f'{name}: unsafe release path')
            continue
        if not p.is_file():
            errors.append(f'{name}: missing file')
            continue
        try:
            text = p.read_text(encoding='utf-8')
        except UnicodeError:
            errors.append(f'{name}: binary content is not allowed in source release')
            continue
        for finding in findings(text):
            errors.append(f'{name}: {finding}')
        for term in private_terms:
            if term and term.lower() in text.lower():
                errors.append(f'{name}: private deny-list match (value withheld)')
        if not text.endswith('\n'):
            errors.append(f'{name}: missing final newline')
    version = (root / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        errors.append('VERSION is not a stable numeric package version')
    conf = (root / 'dkms.conf').read_text()
    if f'PACKAGE_VERSION="{version}"' not in conf:
        errors.append('DKMS version mismatch')
    if f'({version}-1)' not in (root / 'debian/changelog').read_text().splitlines()[0]:
        errors.append('Debian version mismatch')
    provenance = json.loads((root / 'upstream/provenance.json').read_text())
    for name, digest in provenance['files'].items():
        if hashlib.sha256((root / 'upstream' / name).read_bytes()).hexdigest() != digest:
            errors.append(f'upstream/{name}: baseline checksum changed')
    workflow = (root / '.github/workflows/ci.yml').read_text()
    for action in re.findall(r'uses:\s*(\S+)', workflow):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}', action):
            errors.append('CI actions must be pinned to full commit SHA')
    if 'pull_request_target' in workflow:
        errors.append('CI must not execute pull requests with privileged credentials')
    if errors:
        raise ValueError('\n'.join(errors))
    return paths

if __name__ == '__main__':
    try:
        print(f'Source checks passed: {len(check())} release files')
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
