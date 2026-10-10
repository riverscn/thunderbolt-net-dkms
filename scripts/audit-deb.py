#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import gzip
import io
import pathlib
import re
import subprocess
import sys
import tarfile
from privacy import findings

# The only configuration the package installs: module defaults, not network state.
MODPROBE_CONF = 'usr/lib/modprobe.d/thunderbolt-net.conf'
SOURCE_CONF = pathlib.Path(__file__).resolve().parent.parent / 'packaging/thunderbolt-net.conf'


def directives(text):
    return [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith('#')]


EXPECTED = directives(SOURCE_CONF.read_text())
# One options line for this driver's own parameters; never install/softdep
# commands or options for other modules.
ALLOWED = re.compile(r'options thunderbolt_net(?: (?:rx_segment|rx_segment_mtu|rx_page_pool|e2e)=[0-9A-Za-z]+)+')

deb = pathlib.Path(sys.argv[1])
errors = []
seen_conf = False
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
            if option == '--fsys-tarfile' and not (
                    name.startswith(('usr/src/thunderbolt-net-', 'usr/share/doc/thunderbolt-net-dkms/'))
                    or name == MODPROBE_CONF):
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
            if name == MODPROBE_CONF:
                seen_conf = True
                if directives(text) != EXPECTED:
                    errors.append(f'{name}: must match the single options line in {SOURCE_CONF.name}')
            if option == '--ctrl-tarfile' and name in ('postinst','prerm','postrm','preinst'):
                if any(command in text for command in ('rmmod ', 'modprobe -r', 'ip link set', 'systemctl restart')):
                    errors.append(f'{name}: active network mutation in maintainer script')
if len(EXPECTED) != 1 or not ALLOWED.fullmatch(EXPECTED[0]):
    errors.append(f'{SOURCE_CONF.name}: expected one options line for thunderbolt_net parameters')
if not seen_conf:
    errors.append(f'{MODPROBE_CONF}: missing package defaults')
if errors:
    sys.exit('\n'.join(errors))
print('Debian package content and privacy checks passed')
