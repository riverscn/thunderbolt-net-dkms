#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import gzip
import io
import pathlib
import subprocess
import sys
import tarfile
from privacy import findings

deb = pathlib.Path(sys.argv[1])
errors = []
if subprocess.check_output(['dpkg-deb', '-f', str(deb), 'Architecture'], text=True).strip() != 'all':
    errors.append('expected an Architecture: all source-only package')
for option in ('--fsys-tarfile', '--ctrl-tarfile'):
    raw = subprocess.check_output(['dpkg-deb', option, str(deb)])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            if member.isdir():
                continue
            name = member.name.removeprefix('./')
            if not member.isfile() or name.startswith('/') or '..' in pathlib.PurePosixPath(name).parts:
                errors.append(f'{name}: unexpected archive entry')
                continue
            if option == '--fsys-tarfile' and not name.startswith(('usr/src/thunderbolt-net-', 'usr/share/doc/thunderbolt-net-dkms/')):
                errors.append(f'{name}: unexpected installed path')
            content = archive.extractfile(member).read()
            if name.endswith('.gz'):
                content = gzip.decompress(content)
            if content.startswith(b'\x7fELF') or name.endswith(('.ko', '.o', '.key')):
                errors.append(f'{name}: compiled object or key in source-only package')
            try:
                text = content.decode('utf-8')
            except UnicodeError:
                errors.append(f'{name}: unexpected binary payload')
                continue
            errors += [f'{name}: {hit}' for hit in findings(text)]
            if option == '--ctrl-tarfile' and name in ('postinst','prerm','postrm','preinst'):
                if any(command in text for command in ('rmmod ', 'modprobe -r', 'ip link set', 'systemctl restart')):
                    errors.append(f'{name}: active network mutation in maintainer script')
if errors:
    sys.exit('\n'.join(errors))
print('Debian package content and privacy checks passed')
