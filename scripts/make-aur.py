#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Fill packaging/arch/PKGBUILD for a release and write .SRCINFO.

Reads VERSION and, when a SHA256SUMS file is given, the release archive's hash;
writes the result to an output directory ready for `makepkg` or the AUR.
"""
import argparse
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def render(version, sha256):
    text = (ROOT / 'packaging/arch/PKGBUILD').read_text()
    text, n = re.subn(r'^pkgver=.*$', f'pkgver={version}', text, flags=re.M)
    if n != 1:
        raise ValueError('PKGBUILD must set pkgver once')
    text, n = re.subn(r"^sha256sums=\('.*'\)$", f"sha256sums=('{sha256}')", text, flags=re.M)
    if n != 1:
        raise ValueError('PKGBUILD must set sha256sums once')
    return text


def archive_hash(sums, version):
    name = f'thunderbolt-net-dkms-{version}.tar.gz'
    for line in pathlib.Path(sums).read_text().splitlines():
        digest, _, filename = line.partition('  ')
        if filename.strip() == name and re.fullmatch(r'[0-9a-f]{64}', digest):
            return digest
    raise ValueError(f'{name} not listed in {sums}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sums', help='SHA256SUMS of the release (default: keep SKIP)')
    ap.add_argument('--output', default='build/aur', help='directory for PKGBUILD and .SRCINFO')
    args = ap.parse_args()
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        sys.exit('invalid VERSION')
    sha256 = archive_hash(args.sums, version) if args.sums else 'SKIP'
    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'PKGBUILD').write_text(render(version, sha256))
    if shutil.which('makepkg'):
        srcinfo = subprocess.run(['makepkg', '--printsrcinfo'], cwd=out, check=True,
                                 capture_output=True, text=True).stdout
        (out / '.SRCINFO').write_text(srcinfo)
    else:
        print('makepkg not found; .SRCINFO not written', file=sys.stderr)
    print(out / 'PKGBUILD')


if __name__ == '__main__':
    main()
