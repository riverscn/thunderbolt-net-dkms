#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build pinned debug kernels in a disposable Linux build environment."""
import argparse
import hashlib
import pathlib
import subprocess
import tarfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
SHA256 = 'bb7f6d80b387c757b7d14bb93028fcb90f793c5c0d367736ee815a100b3891f0'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=HERE.parent.parent / 'build/debug-kernels')
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / 'linux-7.0.tar.xz'
    if not archive.exists():
        partial = archive.with_suffix('.partial')
        with urllib.request.urlopen('https://cdn.kernel.org/pub/linux/kernel/v7.x/' + archive.name,
                                    timeout=60) as response, partial.open('wb') as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
        partial.rename(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Kernel archive checksum mismatch')
    source = root / 'linux-7.0'
    if not source.exists():
        with tarfile.open(archive) as tf:
            tf.extractall(root, filter='data')
    for mode in ('kasan', 'kcsan'):
        build = root / mode
        build.mkdir(exist_ok=True)
        with (root / (mode + '-build.log')).open('w') as log:
            command = ['make', '-C', str(source), f'O={build}']
            subprocess.run(command + [f'KCONFIG_ALLCONFIG={HERE / (mode + ".config")}', 'allnoconfig'],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
            config = (build / '.config').read_text().splitlines()
            for symbol in ('64BIT', 'SMP', 'PCI', 'INET', 'USB4', 'ARCH_HAS_DMA_OPS',
                           'PAGE_POOL', 'DMA_API_DEBUG', 'SERIAL_8250_CONSOLE', mode.upper()):
                if f'CONFIG_{symbol}=y' not in config:
                    raise ValueError(f'Kernel configuration lacks {symbol}')
            subprocess.run(command + [f'-j{args.jobs}', 'bzImage', 'modules'],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=3600)
        print(f'Built {mode}: {build}', flush=True)


if __name__ == '__main__':
    main()
