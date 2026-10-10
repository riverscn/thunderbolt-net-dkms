#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run kernel tests inside a disposable, diskless QEMU with no external NIC."""
import argparse
import json
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
    kwargs.setdefault('timeout', 120)
    return subprocess.run(argv, check=True, **kwargs)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kernel', required=True, help='installed test kernel release, not uname -r')
    args = ap.parse_args()
    kernel = args.kernel
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+', kernel):
        ap.error('invalid kernel release')
    image = pathlib.Path('/boot') / ('vmlinuz-' + kernel)
    if not image.is_file():
        ap.error('matching kernel image must be installed')
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

        run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-static',
             str(ROOT / 'tests/tcp-session.c'), '-o', str(fs / 'tcp-session')])
        busybox = shutil.which('busybox')
        if not busybox:
            raise RuntimeError('busybox-static is required')
        copy_binary(busybox)
        if busybox != '/bin/busybox':
            shutil.copy2(busybox, fs / 'bin/busybox')
        (fs / 'bin/sh').symlink_to('busybox')
        copy_binary('/usr/sbin/ip' if pathlib.Path('/usr/sbin/ip').exists() else '/usr/bin/ip')
        ip = '/usr/sbin/ip' if (fs / 'usr/sbin/ip').exists() else '/usr/bin/ip'
        for name in ('thunderbolt_net', 'tbnet_rx_test', 'tbnet_path_test', 'tbnet_order_test', 'tbnet_tcp_test'):
            shutil.copy2(ROOT / 'src' / (name + '.ko'), fs / (name + '.ko'))
        dependencies = []
        for name in ('thunderbolt', 'bridge', 'veth'):
            result = run(['modprobe', '-S', kernel, '--show-depends', name], capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if not line.startswith('insmod '):
                    continue
                path = pathlib.Path(line.split()[1])
                destname = path.name.split('.ko')[0] + '.ko'
                if destname in dependencies:
                    continue
                data = path.read_bytes()
                if path.suffix == '.zst':
                    data = run(['zstd', '-dc', str(path)], capture_output=True).stdout
                elif path.suffix == '.xz':
                    data = lzma.decompress(data)
                elif path.suffix == '.gz':
                    data = gzip.decompress(data)
                (fs / destname).write_bytes(data)
                dependencies.append(destname)
        init = '''#!/bin/sh
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
$B insmod /tbnet_rx_test.ko
'''
        init += '\n'.join('$B insmod /' + name for name in dependencies) + '\n'
        init += '''$B insmod /thunderbolt_net.ko rx_segment=1 rx_segment_mtu=1500
test "$($B cat /sys/module/thunderbolt_net/parameters/rx_segment)" = Y
test -r /sys/module/thunderbolt_net/version
test "$($B cat /sys/module/thunderbolt_net/parameters/rx_page_pool)" = N
$B rmmod thunderbolt_net
$B insmod /thunderbolt_net.ko rx_segment=1 rx_page_pool=1 rx_segment_mtu=0
test "$($B cat /sys/module/thunderbolt_net/parameters/rx_page_pool)" = Y
test "$($B cat /sys/module/thunderbolt_net/parameters/rx_segment_mtu)" = 0
$B rmmod thunderbolt_net
$B insmod /tbnet_path_test.ko
'''
        init += f'IP={ip}\n'
        init += """mtu_matrix() {
 echo Y > /sys/module/tbnet_path_test/parameters/mtu_probe
 for row in 1500:1500:0 9000:9000:0 9000:1500:0 9000:1500:1500 1280:1280:0 1500:1500:0; do
  ingress=${row%%:*}
  rest=${row#*:}
  egress=${rest%%:*}
  cap=${rest#*:}
  $IP link set tbtest0 mtu "$ingress"
  $IP link set tbsink0 mtu "$egress"
  echo "$cap" > /sys/module/tbnet_path_test/parameters/mtu_cap
  for n in 2 4 6 8; do
   echo "$n" > /sys/module/tbnet_path_test/parameters/trigger
  done
 done
 echo N > /sys/module/tbnet_path_test/parameters/mtu_probe
}
"""
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
mtu_matrix
$IP link set tbtest0 nomaster
$IP link set tbsink0 nomaster
$IP link del brtest
$IP addr add 192.0.2.1/24 dev tbtest0
$IP addr add 198.51.100.1/24 dev tbsink0
$IP neigh add 192.0.2.10 lladdr 04:00:00:00:00:00 nud permanent dev tbtest0
$IP neigh add 198.51.100.20 lladdr 02:00:00:00:00:09 nud permanent dev tbsink0
$IP -6 addr add 2001:db8:1::1/64 dev tbtest0 nodad
$IP -6 addr add 2001:db8:2::1/64 dev tbsink0 nodad
$IP -6 neigh add 2001:db8:1::10 lladdr 04:00:00:00:00:00 nud permanent dev tbtest0
$IP -6 neigh add 2001:db8:2::20 lladdr 02:00:00:00:00:09 nud permanent dev tbsink0
echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter
echo 0 > /proc/sys/net/ipv4/conf/tbtest0/rp_filter
echo 1 > /proc/sys/net/ipv4/ip_forward
echo 1 > /proc/sys/net/ipv6/conf/all/forwarding
# Disable ICMP rate limits only inside this disposable guest.
echo 0 > /proc/sys/net/ipv4/icmp_ratelimit
echo 0 > /proc/sys/net/ipv4/icmp_ratemask
echo 0 > /proc/sys/net/ipv6/icmp/ratelimit
for n in 1 2 3 4 5 6 7 8; do
 echo "$n" > /sys/module/tbnet_path_test/parameters/trigger
done
mtu_matrix
# Both devices stay jumbo while the FIB route imposes the smaller limit.
$IP link set tbtest0 mtu 9000
$IP link set tbsink0 mtu 9000
echo Y > /sys/module/tbnet_path_test/parameters/mtu_probe
for limit in 1500 1280; do
 $IP route replace 198.51.100.20/32 dev tbsink0 mtu "$limit"
 $IP -6 route replace 2001:db8:2::20/128 dev tbsink0 mtu "$limit"
 echo "$limit" > /sys/module/tbnet_path_test/parameters/route_limit
 for cap in 0 1500 1280; do
  echo "$cap" > /sys/module/tbnet_path_test/parameters/mtu_cap
  for n in 2 4 6 8; do
   echo "$n" > /sys/module/tbnet_path_test/parameters/trigger
  done
 done
 # A small wire-shaped packet control, not a TCP socket/PMTUD session.
 echo 0 > /sys/module/tbnet_path_test/parameters/mtu_cap
 echo 1200 > /sys/module/tbnet_path_test/parameters/probe_payload
 for n in 2 4 6 8; do
  echo "$n" > /sys/module/tbnet_path_test/parameters/trigger
 done
 echo 20001 > /sys/module/tbnet_path_test/parameters/probe_payload
done
echo N > /sys/module/tbnet_path_test/parameters/mtu_probe
'''
        init += '''for legacy in 0 1; do
 $B insmod /tbnet_order_test.ko legacy_header=$legacy
 $IP link set tborder0 addrgenmode none
 $IP link set tborder0 up
 echo 1 > /sys/module/tbnet_order_test/parameters/trigger
 $B rmmod tbnet_order_test
done
'''
        init += r'''$B rmmod tbnet_path_test
$B insmod /tbnet_tcp_test.ko
core=$($B cat /sys/module/tbnet_tcp_test/parameters/core_clamp)
$B rmmod tbnet_tcp_test
policies="preserve auto capped"
test "$core" != Y || policies="$policies unmarked"
echo "TBNET_CORE_ENABLED=$core"
for ipv in 4 6; do
 for policy in $policies; do
  mode=1
  cap=0
  test "$policy" != preserve || mode=0
  test "$policy" != capped || cap=1280
  test "$policy" != unmarked || mode=2
  $B insmod /tbnet_tcp_test.ko mode="$mode" cap="$cap"
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
  $IP addr add 192.0.2.1/24 dev tbrx0
  $IP addr add 198.51.100.1/24 dev out0
  $IP -n sender addr add 192.0.2.10/24 dev tbpeer0
  $IP -n receiver addr add 198.51.100.20/24 dev in0
  $IP -n sender route add default via 192.0.2.1
  $IP -n receiver route add default via 198.51.100.1
  $IP route add 198.51.100.20/32 dev out0 mtu 1280
  $IP -6 addr add 2001:db8:1::1/64 dev tbrx0 nodad
  $IP -6 addr add 2001:db8:2::1/64 dev out0 nodad
  $IP -n sender -6 addr add 2001:db8:1::10/64 dev tbpeer0 nodad
  $IP -n receiver -6 addr add 2001:db8:2::20/64 dev in0 nodad
  $IP -n sender -6 route add default via 2001:db8:1::1
  $IP -n receiver -6 route add default via 2001:db8:2::1
  $IP -6 route add 2001:db8:2::20/128 dev out0 mtu 1280
  echo 0 > /proc/sys/net/ipv4/conf/tbrx0/rp_filter
  dest=198.51.100.20
  test "$ipv" != 6 || dest=2001:db8:2::20
  $IP netns exec receiver /tcp-session server "$dest" &
  server_pid=$!
  $B sleep 1
  echo "TBNET_TCP_CASE ipv=$ipv policy=$policy route_mtu=1280"
  $IP netns exec sender /tcp-session client "$dest"
  for metric in aggregates rebuilt failures min_mss; do
   echo "TBNET_TCP_METRIC $metric=$($B cat /sys/module/tbnet_tcp_test/parameters/$metric)"
  done
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
  $IP netns del sender
  $IP netns del receiver
  $IP link del out0 2>/dev/null || true
  $B rmmod tbnet_tcp_test
 done
done
'''
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
        result = run(command, capture_output=True, text=True, timeout=240)
        build = ROOT / 'build'
        build.mkdir(exist_ok=True)
        (build / ('qemu-' + kernel + '.log')).write_text(result.stdout + result.stderr)
        assert 'TBNET_VM_EXIT=0' in result.stdout, result.stdout[-6000:]
        text = result.stdout.split('TBNET_LOG_BEGIN', 1)[1].split('TBNET_LOG_END', 1)[0]
        assert 'TBNET_TEST SUMMARY tests=39 failures=0' in text, text[-6000:]
        assert len(re.findall(r'TBNET_PATH PASS ', text)) == 16, text[-6000:]
        assert not re.search(r'TBNET_PATH FAIL|BUG:|WARNING:|UBSAN:|Oops:|Call Trace:', text), text[-6000:]
        mtu_lines = re.findall(r'^.*TBNET_MTU (.*)$', text, re.MULTILINE)
        assert len(mtu_lines) == 80, (len(mtu_lines), text[-8000:])
        observations = [dict(field.split('=') for field in line.split())
                        for line in mtu_lines]
        core_clamp = 'TBNET_CORE_ENABLED=Y' in result.stdout
        if core_clamp:
            assert 'TBNET_CORE SUMMARY tests=4 failures=0' in text
        for row in observations:
            assert row['core_clamp'] == str(int(core_clamp)), row
            limit = min(int(row['egress']), int(row['route_limit']) or int(row['egress']))
            packet_size = int(row['payload']) + (40 if row['ipv'] == '4' else 60)
            safe = min(int(row['effective']), packet_size) <= limit
            if safe or core_clamp:
                assert row['valid'] == '1' and row['oversized'] == '0', row
                assert int(row['max_ip_len']) <= limit, row
                assert row['feedback'] == '0', row
            elif row['topology'] == 'route':
                assert row['received'] == '0', row
                assert int(row['feedback']) > 0 and int(row['feedback_mtu']) == limit, row
                assert row['quote_ok'] == row['feedback'], row
            else:
                assert row['received'] == '0' or row['valid'] == '1', row
        (build / ('mtu-' + kernel + '.json')).write_text(
            json.dumps(observations, indent=2) + '\n')
        for row in observations:
            print('MTU observation:', row)
        sessions = []
        for match in re.finditer(
                r'TBNET_TCP_CASE ipv=(\d) policy=(\w+) route_mtu=(\d+)\s+'
                r'TBNET_TCP success=(\d) sent=(\d+) pmtu=(\d+) snd_mss=(\d+) retrans=(\d+)'
                r'\s+TBNET_TCP_METRIC aggregates=(\d+)\s+TBNET_TCP_METRIC rebuilt=(\d+)'
                r'\s+TBNET_TCP_METRIC failures=(\d+)\s+TBNET_TCP_METRIC min_mss=(\d+)',
                result.stdout):
            keys = ('ipv', 'policy', 'route_mtu', 'success', 'sent', 'pmtu',
                    'snd_mss', 'retrans', 'aggregates', 'rebuilt', 'failures', 'min_mss')
            row = dict(zip(keys, match.groups()))
            assert row['failures'] == '0' and int(row['aggregates']) > 0, row
            if row['policy'] in ('preserve', 'capped') or (core_clamp and row['policy'] == 'auto'):
                assert row['success'] == '1', row
            if row['policy'] == 'preserve':
                assert row['rebuilt'] == '0' and row['pmtu'] == '1280', row
                assert int(row['min_mss']) < 1280, row
            else:
                assert int(row['rebuilt']) > 0, row
            sessions.append(row)
            print('TCP observation:', row)
        assert len(sessions) == (8 if core_clamp else 6), result.stdout[-10000:]
        (build / ('tcp-' + kernel + '.json')).write_text(
            json.dumps(sessions, indent=2) + '\n')
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
        print(f'QEMU passed: 39 unit tests, 16 bridge/router cases, 80 MTU/PMTU observations, {len(sessions)} TCP sessions, GRO ordering, driver load/unload')

if __name__ == '__main__':
    main()
