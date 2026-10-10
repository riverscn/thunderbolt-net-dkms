#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run RX model or intentional detector controls only in a diskless QEMU guest."""
import argparse
import gzip
import json
import pathlib
import re
import shutil
import subprocess
import tempfile

from extract import ROOT, prepare


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--kernel', help='installed Linux 7.0 release')
    ap.add_argument('--kdir', type=pathlib.Path, help='custom built kernel directory')
    ap.add_argument('--image', type=pathlib.Path, help='matching custom bzImage')
    ap.add_argument('--mode', choices=['normal', 'negative', 'startup-race', 'shutdown-race', 'sanity-kasan',
                    'sanity-kcsan', 'sanity-dma'], default='normal')
    ap.add_argument('--accel', choices=['tcg', 'kvm'], default='tcg')
    ap.add_argument('--output', type=pathlib.Path, default=ROOT / 'build/rx-page-pool')
    args = ap.parse_args()
    if args.kernel:
        if args.kdir or args.image or not re.fullmatch(r'7\.0[\w.+-]*', args.kernel):
            ap.error('use --kernel for Linux 7.0, or --kdir and --image together')
        args.kdir = pathlib.Path('/lib/modules') / args.kernel / 'build'
        args.image = pathlib.Path('/boot') / ('vmlinuz-' + args.kernel)
    if not args.kdir or not args.image or not args.image.is_file():
        ap.error('a built Linux 7.0 kernel and matching image are required')
    config_path = args.kdir / '.config'
    config = config_path.read_text()
    for required in ('CONFIG_SMP=y', 'CONFIG_ARCH_HAS_DMA_OPS=y', 'CONFIG_PAGE_POOL=y'):
        if required not in config.splitlines():
            ap.error(f'kernel lacks {required}')
    detector = {'sanity-kasan': 'CONFIG_KASAN=y', 'sanity-kcsan': 'CONFIG_KCSAN=y',
                'sanity-dma': 'CONFIG_DMA_API_DEBUG=y'}.get(args.mode)
    if detector and detector not in config.splitlines():
        ap.error(f'control requires {detector}')
    args.output = args.output.resolve()
    test = args.output / 'test'
    prepare(test)
    subprocess.run(['make', '-C', str(test), f'KDIR={args.kdir.resolve()}', '-j2'],
                   check=True, timeout=120)
    sanity = args.mode.startswith('sanity-')
    name = 'tbnet_sanity' if sanity else 'tbnet_ring_test'
    init = '''#!/bin/sh
export PATH=/bin
B=/bin/busybox
$B mount -t devtmpfs devtmpfs /dev
exec >/dev/console 2>&1
finish() {
 status=$?
 echo RINGLAB_LOG_BEGIN
 $B dmesg
 echo RINGLAB_LOG_END
 echo "RINGLAB_VM_EXIT=$status"
 $B poweroff -f
}
trap finish EXIT
set -eu
$B mount -t proc proc /proc
$B mount -t sysfs sysfs /sys
$B mount -t debugfs debugfs /sys/kernel/debug
if test -r /sys/kernel/debug/dma-api/error_count; then
 echo "RINGLAB_DMA_BEFORE=$($B cat /sys/kernel/debug/dma-api/error_count)"
fi
'''
    option = {'startup-race': ' startup_race=1',
              'shutdown-race': ' shutdown_race=1', 'negative': ' negative=1',
              'sanity-dma': ' dma_negative=1'}.get(args.mode, '')
    init += f'$B taskset -c 0 $B insmod /{name}.ko{option}\n'
    if not sanity:
        init += 'test "$($B cat /sys/module/tbnet_ring_test/parameters/result)" = 0\n'
    init += f'$B rmmod {name}\n'
    comparison = '-gt 0' if args.mode == 'sanity-dma' else '= 0'
    init += '''if test -r /sys/kernel/debug/dma-api/error_count; then
 echo "RINGLAB_DMA_AFTER=$($B cat /sys/kernel/debug/dma-api/error_count)"
 test "$($B cat /sys/kernel/debug/dma-api/error_count)" ''' + comparison + '\nfi\n'
    with tempfile.TemporaryDirectory(prefix='tbnet-ring-') as temporary:
        fs = pathlib.Path(temporary)
        for part in ('bin', 'dev', 'proc', 'sys', 'tmp'):
            (fs / part).mkdir()
        busybox = shutil.which('busybox')
        if not busybox:
            raise RuntimeError('busybox-static is required')
        shutil.copy2(busybox, fs / 'bin/busybox')
        (fs / 'bin/sh').symlink_to('busybox')
        shutil.copy2(test / (name + '.ko'), fs / (name + '.ko'))
        (fs / 'init').write_text(init)
        (fs / 'init').chmod(0o755)
        names = b'.\0' + b'\0'.join(str(p.relative_to(fs)).encode()
                                  for p in sorted(fs.rglob('*'))) + b'\0'
        cpio = subprocess.run(['cpio', '--null', '-o', '--format=newc', '--quiet'],
                              cwd=fs, input=names, capture_output=True,
                              check=True, timeout=30).stdout
    archive = args.output / (args.mode + '.init.gz')
    archive.write_bytes(gzip.compress(cpio, mtime=0))
    command = ['qemu-system-x86_64', '-machine', f'q35,accel={args.accel}',
               '-cpu', 'host' if args.accel == 'kvm' else 'max', '-m', '1536',
               '-smp', '2', '-nodefaults', '-no-user-config', '-display', 'none',
               '-monitor', 'none', '-serial', 'stdio', '-nic', 'none', '-no-reboot',
               '-kernel', str(args.image.resolve()), '-initrd', str(archive),
               '-append', 'console=ttyS0 rdinit=/init panic=-1 cma=64M quiet '
               'log_buf_len=16M kasan_multi_shot kcsan.early_enable=1 '
               'kcsan.skip_watch=100 kcsan.udelay_task=50']
    log = args.output / (args.mode + '.log')
    with log.open('w') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=300)
    text = log.read_text()
    body = text.split('RINGLAB_LOG_BEGIN', 1)[-1].split('RINGLAB_LOG_END', 1)[0]
    passed = result.returncode == 0 and 'RINGLAB_VM_EXIT=0' in text
    if 'CONFIG_KCSAN=y' in config.splitlines():
        passed = passed and 'kcsan: enabled early' in body
    if args.mode == 'sanity-dma':
        passed = passed and all(s in body for s in
                               ('DMA-API:', 'different size', 'intentional_dma_size_mismatch'))
    elif sanity:
        passed = passed and ('BUG: KASAN:' if args.mode == 'sanity-kasan' else 'BUG: KCSAN:') in body
    else:
        summary = re.search(r'RINGLAB SUMMARY result=0 cases=(\d+) failures=0 '
                            r'maps=(\d+) unmaps=(\d+) live=0', body)
        passed = passed and summary is not None
        if summary:
            expected_cases = {'negative': 1, 'startup-race': 2,
                              'shutdown-race': 1}.get(args.mode, 63)
            passed = passed and int(summary[1]) == expected_cases
            passed = passed and int(summary[2]) == int(summary[3]) > 0
        passed = passed and not re.search(r'BUG:|WARNING:|Oops:|Call Trace:|RINGLAB FAIL', body)
    report = {'mode': args.mode, 'passed': bool(passed),
              'summary': re.findall(r'RINGLAB (?:SUMMARY|ENV)[^\n]*', body)}
    (args.output / (args.mode + '.json')).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    if not passed:
        print(text[-16000:])
        raise SystemExit(1)


if __name__ == '__main__':
    main()
