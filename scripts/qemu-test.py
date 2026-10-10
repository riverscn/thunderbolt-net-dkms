#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run kernel tests inside a disposable, diskless QEMU with no external NIC."""
import argparse
import gzip
import lzma
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

def run(argv, **kwargs):
    return subprocess.run(argv, check=True, timeout=120, **kwargs)


def copy_module(path, fs):
    """Copy a (possibly compressed) module into the guest root; return its name."""
    path = pathlib.Path(path)
    destname = path.name.split('.ko')[0] + '.ko'
    data = path.read_bytes()
    if path.suffix == '.zst':
        data = run(['zstd', '-dc', str(path)], capture_output=True).stdout
    elif path.suffix == '.xz':
        data = lzma.decompress(data)
    elif path.suffix == '.gz':
        data = gzip.decompress(data)
    (fs / destname).write_bytes(data)
    return destname


INIT_HEADER = '''#!/bin/sh
export PATH=/bin
B=/bin/busybox
$B mount -t devtmpfs devtmpfs /dev
exec >/dev/console 2>&1
finish() {
 status=$?
 echo TBNET_LOG_BEGIN
 $B dmesg
 echo TBNET_LOG_END
 echo "TBNET_VM_EXIT=$status"
 $B poweroff -f
}
trap finish EXIT
set -eu
$B mount -t proc proc /proc
$B mount -t sysfs sysfs /sys
'''


def boot(base, fs, init, image, log_name):
    """Boot the guest with this init; return its kernel log after a clean exit."""
    (fs / 'init').write_text(init)
    (fs / 'init').chmod(0o755)
    names = b'.\0' + b'\0'.join(str(p.relative_to(fs)).encode() for p in sorted(fs.rglob('*'))) + b'\0'
    cpio = run(['cpio', '--null', '-o', '--format=newc', '--quiet'], input=names,
               cwd=fs, capture_output=True).stdout
    archive = base / 'initramfs.gz'
    archive.write_bytes(gzip.compress(cpio, mtime=0))
    command = ['qemu-system-x86_64', '-machine', 'q35,accel=tcg', '-cpu', 'max',
               '-m', '1024', '-smp', '1', '-nodefaults', '-no-user-config',
               '-display', 'none', '-monitor', 'none', '-serial', 'stdio',
               '-nic', 'none', '-no-reboot', '-kernel', str(image),
               '-initrd', str(archive), '-append', 'console=ttyS0 rdinit=/init panic=-1 quiet']
    result = run(command, capture_output=True, text=True)
    build = ROOT / 'build'
    build.mkdir(exist_ok=True)
    (build / log_name).write_text(result.stdout + result.stderr)
    assert 'TBNET_VM_EXIT=0' in result.stdout, result.stdout[-6000:]
    text = result.stdout.split('TBNET_LOG_BEGIN', 1)[1].split('TBNET_LOG_END', 1)[0]
    assert not re.search(r'BUG:|WARNING:|UBSAN:|Oops:|Call Trace:', text), text[-6000:]
    return text


def package_options():
    """The options the package installs, from packaging/thunderbolt-net.conf."""
    lines = [l.split() for l in (ROOT / 'packaging/thunderbolt-net.conf').read_text().splitlines()
             if l.strip() and not l.lstrip().startswith('#')]
    if len(lines) != 1 or lines[0][:2] != ['options', 'thunderbolt_net']:
        raise ValueError('packaging/thunderbolt-net.conf must hold one thunderbolt_net options line')
    return lines[0][2:]


def installed_check(kernel, image, base, fs):
    """Load the installed module exactly as modprobe resolves it and check sysfs."""
    result = run(['modprobe', '-S', kernel, '--show-depends', 'thunderbolt_net'],
                 capture_output=True, text=True)
    commands = [l.split() for l in result.stdout.splitlines() if l.startswith('insmod ')]
    if not commands:
        raise AssertionError('modprobe resolved no module: ' + result.stdout)
    *dependencies, (_, target, *options) = commands
    if '/updates/dkms/' not in target:
        raise AssertionError('modprobe did not select the DKMS module: ' + target)
    expected = package_options()
    if options != expected:
        raise AssertionError(f'modprobe options {options} differ from package {expected}')
    for option in options:
        if not re.fullmatch(r'[a-z_0-9]+=[0-9A-Za-z]+', option):
            raise AssertionError('unexpected option ' + option)
    init = INIT_HEADER
    for _, path, *extra in dependencies:
        init += f'$B insmod /{copy_module(path, fs)} {" ".join(extra)}\n'
    init += f'$B insmod /{copy_module(target, fs)} {" ".join(options)}\n'
    for option in options:
        name, value = option.split('=', 1)
        # Boolean parameters read back as Y/N; integers read back unchanged.
        shown = {'0': 'N', '1': 'Y'}.get(value, value) if name in BOOL_PARAMS else value
        init += f'test "$($B cat /sys/module/thunderbolt_net/parameters/{name})" = {shown}\n'
    init += 'test -r /sys/module/thunderbolt_net/version\n$B rmmod thunderbolt_net\n'
    text = boot(base, fs, init, image, f'qemu-installed-{kernel}.log')
    assert 'unknown parameter' not in text, text[-6000:]
    print('QEMU passed: installed module loaded with package defaults ' + ' '.join(options))


BOOL_PARAMS = ('rx_segment', 'rx_page_pool', 'e2e')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kernel', required=True, help='installed test kernel release, not uname -r')
    ap.add_argument('--installed', action='store_true',
                    help='load the installed DKMS module with its modprobe.d options instead')
    args = ap.parse_args()
    kernel = args.kernel
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+', kernel):
        ap.error('invalid kernel release')
    image = pathlib.Path('/boot') / ('vmlinuz-' + kernel)
    if not image.is_file():
        ap.error('matching kernel image must be installed')
    if not args.installed:
        run(['make', 'test-modules', f'KERNELRELEASE={kernel}', '-j2'], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='tbnet-qemu-') as temporary:
        base = pathlib.Path(temporary)
        fs = base / 'root'
        for part in ('bin','dev','proc','sys','tmp','lib/modules'):
            (fs / part).mkdir(parents=True, exist_ok=True)

        def copy_binary(path):
            target = fs / path.lstrip('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            libraries = subprocess.run(['ldd', path], text=True, capture_output=True, timeout=10)
            if libraries.returncode and 'not a dynamic executable' not in libraries.stdout + libraries.stderr:
                raise RuntimeError('Cannot enumerate executable dependencies')
            for library in re.findall(r'(/[^\s()]+)', libraries.stdout):
                dest = fs / library.lstrip('/')
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(library, dest)

        busybox = shutil.which('busybox')
        if not busybox:
            raise RuntimeError('busybox-static is required')
        copy_binary(busybox)
        if busybox != '/bin/busybox':
            shutil.copy2(busybox, fs / 'bin/busybox')
        (fs / 'bin/sh').symlink_to('busybox')
        if args.installed:
            installed_check(kernel, image, base, fs)
            return
        copy_binary('/usr/sbin/ip' if pathlib.Path('/usr/sbin/ip').exists() else '/usr/bin/ip')
        ip = '/usr/sbin/ip' if (fs / 'usr/sbin/ip').exists() else '/usr/bin/ip'
        for name in ('thunderbolt_net', 'tbnet_rx_test', 'tbnet_path_test', 'tbnet_order_test'):
            shutil.copy2(ROOT / 'src' / (name + '.ko'), fs / (name + '.ko'))
        dependencies = []
        for name in ('thunderbolt', 'bridge'):
            result = run(['modprobe', '-S', kernel, '--show-depends', name], capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if not line.startswith('insmod '):
                    continue
                destname = copy_module(line.split()[1], fs)
                if destname not in dependencies:
                    dependencies.append(destname)
        init = INIT_HEADER + '''$B insmod /tbnet_rx_test.ko
'''
        init += '\n'.join('$B insmod /' + name for name in dependencies) + '\n'
        init += '''$B insmod /thunderbolt_net.ko rx_segment=1 rx_segment_mtu=1500
test "$($B cat /sys/module/thunderbolt_net/parameters/rx_segment)" = Y
test -r /sys/module/thunderbolt_net/version
test "$($B cat /sys/module/thunderbolt_net/parameters/rx_page_pool)" = N
$B rmmod thunderbolt_net
$B insmod /thunderbolt_net.ko rx_segment=1 rx_page_pool=1
test "$($B cat /sys/module/thunderbolt_net/parameters/rx_page_pool)" = Y
$B rmmod thunderbolt_net
$B insmod /tbnet_path_test.ko
'''
        init += f'IP={ip}\n'
        init += '''$IP link set lo up
$IP link set tbtest0 addrgenmode none
$IP link set tbsink0 addrgenmode none
$IP link add brtest type bridge
$IP link set brtest addrgenmode none
$IP link set tbtest0 master brtest
$IP link set tbsink0 master brtest
$IP link set tbtest0 up
$IP link set tbsink0 up
$IP link set brtest up
$B sleep 1
for n in 1 2 3 4 5 6 7 8; do
 echo "$n" > /sys/module/tbnet_path_test/parameters/trigger
done
$IP link set tbtest0 nomaster
$IP link set tbsink0 nomaster
$IP link del brtest
$IP addr add 192.0.2.1/24 dev tbtest0
$IP addr add 198.51.100.1/24 dev tbsink0
$IP neigh add 198.51.100.20 lladdr 02:00:00:00:00:09 nud permanent dev tbsink0
$IP -6 addr add 2001:db8:1::1/64 dev tbtest0 nodad
$IP -6 addr add 2001:db8:2::1/64 dev tbsink0 nodad
$IP -6 neigh add 2001:db8:2::20 lladdr 02:00:00:00:00:09 nud permanent dev tbsink0
echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter
echo 0 > /proc/sys/net/ipv4/conf/tbtest0/rp_filter
echo 1 > /proc/sys/net/ipv4/ip_forward
echo 1 > /proc/sys/net/ipv6/conf/all/forwarding
for n in 1 2 3 4 5 6 7 8; do
 echo "$n" > /sys/module/tbnet_path_test/parameters/trigger
done
'''
        init += '''for legacy in 0 1; do
 $B insmod /tbnet_order_test.ko legacy_header=$legacy
 $IP link set tborder0 addrgenmode none
 $IP link set tborder0 up
 echo 1 > /sys/module/tbnet_order_test/parameters/trigger
 $B rmmod tbnet_order_test
done
'''
        text = boot(base, fs, init, image, 'qemu-' + kernel + '.log')
        assert 'TBNET_TEST SUMMARY tests=27 failures=0' in text, text[-6000:]
        assert len(re.findall(r'TBNET_PATH PASS ', text)) == 16, text[-6000:]
        assert 'TBNET_PATH FAIL' not in text, text[-6000:]
        summaries = re.findall(
            r'TBNET_ORDER SUMMARY cases=(\d+) reordered=(\d+) errors=(\d+) '
            r'header=(\d+) headroom=(\d+)', text)
        assert len(summaries) == 2, text[-6000:]
        fixed, legacy = [tuple(map(int, item)) for item in summaries]
        assert fixed == (256, 0, 0, 14, 12), fixed
        # Different supported kernels may hold/merge different packet subsets.
        # Require real reordering, never loss or corruption, in the old layout.
        assert legacy[0] == 256 and 0 < legacy[1] <= 256, legacy
        assert legacy[2:] == (0, 26, 0), legacy
        assert 'TBNET_ORDER ERROR' not in text, text[-6000:]
        print(f'GRO ordering: 256 corrected cases passed; legacy control reordered {legacy[1]}/256')
        print('QEMU passed: 27 unit tests, 16 bridge/router cases, GRO ordering, driver load/unload')

if __name__ == '__main__':
    main()
