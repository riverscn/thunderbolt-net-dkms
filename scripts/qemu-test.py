#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run kernel tests inside a disposable, diskless QEMU with no external NIC."""
import argparse
import gzip
import json
import lzma
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

def run(argv, **kwargs):
    kwargs.setdefault('timeout', 120)
    return subprocess.run(argv, check=True, **kwargs)


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


def boot(base, fs, init, image, log_name, timeout=120):
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
               '-initrd', str(archive), '-append', 'console=ttyS0 rdinit=/init panic=-1 log_buf_len=4M quiet']
    result = run(command, capture_output=True, text=True, timeout=timeout)
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
        for part in ('bin','dev','proc','sys','tmp','run','etc','lib/modules'):
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
        run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-static',
             str(ROOT / 'tests/tcp-session.c'), '-o', str(fs / 'tcp-session')])
        for name in ('thunderbolt_net', 'tbnet_rx_test', 'tbnet_path_test', 'tbnet_order_test', 'tbnet_tcp_test'):
            shutil.copy2(ROOT / 'src' / (name + '.ko'), fs / (name + '.ko'))
        dependencies = []
        for name in ('thunderbolt', 'bridge', 'veth', '8021q'):
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
test "$($B cat /sys/module/thunderbolt_net/parameters/rx_segment_mtu)" = 1280
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
        # Real TCP sessions through the metadata-loss link. The host routes
        # the sender's namespace to the receiver's over a veth whose route is
        # limited to 1280 while every interface stays at 9000, so the sender
        # learns PMTU 1280 and keeps sending TSO aggregates of smaller
        # segments. The advmss case lets the receiver's SYN-ACK carry the
        # limit instead, so no ICMP is needed. Results go to the kernel log.
        init += r'''$B rmmod tbnet_path_test
for ipv in 4 6; do
 for policy in preserve capped oversized learned learned_mss learned_vlan; do
  mode=1
  cap=1280
  learn=0
  advmss=
  rx=tbrx0
  peer=tbpeer0
  case "$policy" in
   preserve) mode=0 ;;
   oversized) cap=1500 ;;
   learned) cap=1500; learn=1 ;;
   learned_mss) cap=1500; learn=1; advmss=1240; test "$ipv" != 6 || advmss=1220 ;;
   learned_vlan) cap=1500; learn=1; rx=tbrx0.7; peer=tbpeer0.7 ;;
  esac
  $B insmod /tbnet_tcp_test.ko mode="$mode" cap="$cap" learn="$learn"
  $IP netns add sender
  $IP netns add receiver
  $IP link set tbpeer0 netns sender
  $IP link add out0 type veth peer name in0
  $IP link set in0 netns receiver
  $IP link set tbrx0 mtu 9000 up
  $IP link set out0 mtu 9000 up
  $IP -n sender link set lo up
  $IP -n sender link set tbpeer0 mtu 9000 up
  $IP -n receiver link set lo up
  $IP -n receiver link set in0 mtu 9000 up
  if test "$rx" != tbrx0; then
   # In-band 802.1Q tags on both the data and the control packets.
   $IP link add link tbrx0 name "$rx" type vlan id 7
   $IP link set "$rx" mtu 9000 up
   $IP -n sender link add link tbpeer0 name "$peer" type vlan id 7
   $IP -n sender link set "$peer" mtu 9000 up
  fi
  $IP addr add 192.0.2.1/24 dev "$rx"
  $IP addr add 198.51.100.1/24 dev out0
  $IP -n sender addr add 192.0.2.10/24 dev "$peer"
  $IP -n receiver addr add 198.51.100.20/24 dev in0
  $IP -n sender route add default via 192.0.2.1
  $IP -n receiver route add default via 198.51.100.1 ${advmss:+advmss $advmss}
  $IP route add 198.51.100.20/32 dev out0 mtu 1280
  $IP -6 addr add 2001:db8:1::1/64 dev "$rx" nodad
  $IP -6 addr add 2001:db8:2::1/64 dev out0 nodad
  $IP -n sender -6 addr add 2001:db8:1::10/64 dev "$peer" nodad
  $IP -n receiver -6 addr add 2001:db8:2::20/64 dev in0 nodad
  $IP -n sender -6 route add default via 2001:db8:1::1
  $IP -n receiver -6 route add default via 2001:db8:2::1 ${advmss:+advmss $advmss}
  $IP -6 route add 2001:db8:2::20/128 dev out0 mtu 1280
  echo 0 > /proc/sys/net/ipv4/conf/tbrx0/rp_filter
  test "$rx" = tbrx0 || echo 0 > "/proc/sys/net/ipv4/conf/$rx/rp_filter"
  dest=198.51.100.20
  test "$ipv" != 6 || dest=2001:db8:2::20
  $IP netns exec receiver /tcp-session server "$dest" &
  server_pid=$!
  $B sleep 1
  echo "TBNET_TCP_CASE ipv=$ipv policy=$policy cap=$cap learn=$learn route_mtu=1280" > /dev/kmsg
  $IP netns exec sender /tcp-session client "$dest" > /dev/kmsg
  metrics=
  for metric in aggregates rebuilt failures min_mss rebuilt_gso plain_over_path max_plain_iplen bound_mss bound_icmp bound_hits; do
   metrics="$metrics $metric=$($B cat /sys/module/tbnet_tcp_test/parameters/$metric)"
  done
  echo "TBNET_TCP_METRICS$metrics" > /dev/kmsg
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
  $IP netns del sender
  $IP netns del receiver
  $IP link del out0 2>/dev/null || true
  test "$rx" = tbrx0 || $IP link del "$rx"
  $B rmmod tbnet_tcp_test
 done
done
'''
        text = boot(base, fs, init, image, 'qemu-' + kernel + '.log', timeout=420)
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
        sessions = tcp_sessions(text)
        (ROOT / 'build' / ('tcp-' + kernel + '.json')).write_text(json.dumps(sessions, indent=2) + '\n')
        print(f'QEMU passed: 27 unit tests, 16 bridge/router cases, {len(sessions)} TCP sessions, '
              'GRO ordering, driver load/unload')


def tcp_sessions(text):
    """Check the TCP session results in the kernel log; return them as rows."""
    pattern = (r'TBNET_TCP_CASE (?P<case>[^\n]*)\n[^\n]*TBNET_TCP (?P<result>[^\n]*)\n'
               r'[^\n]*TBNET_TCP_METRICS (?P<metrics>[^\n]*)')
    sessions = []
    for match in re.finditer(pattern, text):
        row = {}
        for part in ('case', 'result', 'metrics'):
            row.update(field.split('=', 1) for field in match.group(part).split())
        row = {key: int(value) if value.isdigit() else value for key, value in row.items()}
        print('TCP session:', row)
        assert row['failures'] == 0 and row['aggregates'] > 0, row
        if row['policy'] == 'preserve':
            # A NIC that keeps segment boundaries: no normalization needed.
            assert row['success'] == 1 and row['rebuilt'] == 0, row
            assert row['pmtu'] == 1280 and row['min_mss'] < 1280, row
            continue
        assert row['rebuilt'] > 0 and row['rebuilt_gso'] > 0, row
        if row['policy'] == 'capped':
            # A cap at the path limit is correct without any learning.
            assert row['success'] == 1 and row['plain_over_path'] == 0, row
            assert row['bound_hits'] == 0, row
        elif row['policy'] == 'oversized':
            # A cap above the path limit is the stall under study; its
            # outcome depends on kernel TCP recovery and is only recorded.
            assert row['bound_hits'] == 0, row
        elif row['policy'] in ('learned', 'learned_vlan'):
            # The same oversized cap, corrected by the ICMP feedback that
            # passes through the host toward the sender; also through a VLAN.
            assert row['success'] == 1, row
            assert row['bound_icmp'] > 0 and row['bound_hits'] > 0, row
            assert row['plain_over_path'] == 0, row
        elif row['policy'] == 'learned_mss':
            # The receiver's SYN-ACK carries the limit: no ICMP occurs.
            assert row['success'] == 1, row
            assert row['bound_mss'] > 0 and row['bound_icmp'] == 0, row
            assert row['bound_hits'] > 0 and row['plain_over_path'] == 0, row
        sessions.append(row)
    assert len(sessions) == 10, text[-8000:]
    return sessions

if __name__ == '__main__':
    main()
