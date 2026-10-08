#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Export only audited text sources; never include a working directory wholesale."""
import datetime
import email.utils
import gzip
import importlib.util
import io
import os
import pathlib
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('check_source', ROOT / 'scripts/check-source.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

def create(destination=None):
    paths = checker.check()
    version = (ROOT / 'VERSION').read_text().strip()
    changelog = (ROOT / 'debian/changelog').read_text()
    date = next(line.rsplit('  ', 1)[1] for line in changelog.splitlines() if line.startswith(' -- '))
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', email.utils.parsedate_to_datetime(date).timestamp()))
    output = pathlib.Path(destination) if destination else ROOT / 'dist' / f'thunderbolt-net-dkms-{version}.tar.gz'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=epoch) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for name in paths:
                content = (ROOT / name).read_bytes()
                info = tarfile.TarInfo(f'thunderbolt-net-dkms-{version}/{name}')
                info.size = len(content)
                info.mtime = epoch
                info.uid = info.gid = 0
                info.uname = info.gname = 'root'
                info.mode = 0o755 if name.endswith('.sh') or name == 'debian/rules' else 0o644
                tar.addfile(info, io.BytesIO(content))
    return output

if __name__ == '__main__':
    print(create())
