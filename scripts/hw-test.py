#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Manual hardware test helper for a Linux host with a macOS Thunderbolt peer.

Runs on the Linux host only. It never installs packages or switches DKMS
versions; `reload` changes module parameters and needs root. Raw iperf3 JSON
stays in the output directory. `summary` prints a redacted report to share.
"""
import argparse
import csv
import datetime
import errno
import ipaddress
import json
import os
import pathlib
import re
import shlex
import shutil
import socket
import statistics
import subprocess
import sys
import time

MODULE = 'thunderbolt_net'
DRIVER = 'thunderbolt-net'
# Leading columns match docs/iperf3-results.csv; the rest identify the build.
CSV_FIELDS = [
    'phase', 'direction', 'sample', 'receiver_gbit_s', 'tcp_retransmits',
    'host_softirq_seconds_per_gib', 'rx_errors', 'rx_dropped', 'bad_checksums',
    'invalid_packets', 'tx_errors', 'tx_dropped', 'softnet_dropped',
    'rx_normalized_packets', 'transport_verified', 'kernel_alerts',
    'module_version', 'srcversion', 'params', 'kernel', 'mtu', 'streams', 'seconds',
]
SYSFS_COUNTERS = ('rx_bytes', 'tx_bytes', 'rx_errors', 'rx_dropped',
                  'tx_errors', 'tx_dropped')
ALERT = re.compile(r'WARNING:|Oops|BUG:|Call Trace|panic|hung_task|'
                   r'soft lockup|refcount_t|list_add corruption')
RELEVANT = re.compile(r'thunderbolt|tbnet|usb4', re.I)

# --- redaction -------------------------------------------------------------

_IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
_IPV6 = re.compile(r'(?<![\w:])[0-9A-Fa-f:]*:[0-9A-Fa-f:]*:[0-9A-Fa-f]*(?:%[\w.-]+)?')
_MAC = re.compile(r'\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b')
_UUID = re.compile(r'\b[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\b')
_HOME = re.compile(r'/(?:home|Users)/[^/\s]+')


def _keep_ip(text):
    """Keep non-addresses (times, versions), loopback and documentation ranges."""
    try:
        addr = ipaddress.ip_address(text.split('%')[0])
    except ValueError:
        return True
    docs = ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '2001:db8::/32')
    return addr.is_loopback or addr.is_unspecified or any(
        addr in ipaddress.ip_network(n) for n in docs)


def redact(text, extra=()):
    """Replace addresses, identifiers and user-supplied terms with labels."""
    for term, label in extra:
        if term:
            # Whole names only: hostname "pve" must not rewrite "7.0.14-23-pve".
            text = re.sub(r'(?<![\w.-])' + re.escape(term) + r'(?![\w-])', label, text)
    text = _UUID.sub('<uuid>', text)
    text = _MAC.sub('<mac>', text)
    text = _IPV6.sub(lambda m: m.group() if _keep_ip(m.group()) else '<ipv6>', text)
    text = _IPV4.sub(lambda m: m.group() if _keep_ip(m.group()) else '<ipv4>', text)
    return _HOME.sub('<home>', text)

# --- system probes ---------------------------------------------------------


def cmd(argv, timeout=30):
    """Run a probe; return stdout, or a short marker when it cannot run."""
    if not shutil.which(argv[0]):
        return f'({argv[0]} not installed)'
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f'({argv[0]} timed out)'
    return (p.stdout or p.stderr).strip()


def read(path, default=''):
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return default


def tb_interfaces():
    names = []
    for dev in sorted(pathlib.Path('/sys/class/net').glob('*')):
        driver = dev / 'device' / 'driver'
        if driver.exists() and os.path.basename(os.readlink(driver)) == DRIVER:
            names.append(dev.name)
    return names


def pick_iface(name):
    if name:
        if not pathlib.Path('/sys/class/net', name).exists():
            sys.exit(f'interface {name} does not exist')
        return name
    found = tb_interfaces()
    if len(found) != 1:
        sys.exit('pass --iface: found %s Thunderbolt interfaces (%s)'
                 % (len(found), ', '.join(found) or 'none'))
    return found[0]


def master_of(iface):
    link = pathlib.Path('/sys/class/net', iface, 'master')
    return os.path.basename(os.readlink(link)) if link.exists() else ''


def module_info():
    base = pathlib.Path('/sys/module', MODULE)
    params = {p.name: read(p) for p in sorted((base / 'parameters').glob('*'))}
    return {
        'version': read(base / 'version', 'not loaded'),
        'srcversion': read(base / 'srcversion'),
        'params': ' '.join(f'{k}={v}' for k, v in params.items()),
        'path': cmd(['modinfo', '-n', MODULE]),
    }


def parse_ethtool_stats(text):
    stats = {}
    for line in text.splitlines():
        key, sep, value = line.strip().rpartition(':')
        if sep and value.strip().isdigit():
            stats[key.strip()] = int(value)
    return stats


def parse_softnet(text):
    """Sum the dropped column (second field, hex) over all CPUs."""
    return sum(int(line.split()[1], 16) for line in text.splitlines() if line.split())


def softirq_ticks(text):
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == 'cpu':
            return int(fields[7])
    return 0


def snapshot(iface):
    sysfs = {k: int(read(f'/sys/class/net/{iface}/statistics/{k}', '0'))
             for k in SYSFS_COUNTERS}
    sysfs.update(parse_ethtool_stats(cmd(['ethtool', '-S', iface])))
    sysfs['softnet_dropped'] = parse_softnet(read('/proc/net/softnet_stat'))
    sysfs['softirq_ticks'] = softirq_ticks(read('/proc/stat'))
    return sysfs


def os_name():
    m = re.search(r'^PRETTY_NAME="?([^"\n]*)', read('/etc/os-release'), re.M)
    return m.group(1) if m else '?'


def default_route_dev():
    out = cmd(['ip', 'route', 'get', '1.1.1.1'])
    m = re.search(r'\bdev (\S+)', out)
    return m.group(1) if m else ''


class KernelLog:
    """Collect new /dev/kmsg records; reports unavailability instead of failing."""

    def __init__(self):
        self.fd = None
        try:
            self.fd = os.open('/dev/kmsg', os.O_RDONLY | os.O_NONBLOCK)
            os.lseek(self.fd, 0, os.SEEK_END)
        except OSError:
            self.fd = None

    def drain(self):
        if self.fd is None:
            return None
        lines = []
        while True:
            try:
                record = os.read(self.fd, 8192).decode(errors='replace')
            except OSError as exc:
                if exc.errno == errno.EPIPE:
                    continue  # ring buffer overwrote unread records
                break
            _, _, msg = record.partition(';')
            lines.append(msg.split('\n', 1)[0])
        return lines

# --- iperf3 ----------------------------------------------------------------


def run_order(repeat):
    """Alternate directions, mirroring each pair: up, down, down, up, ..."""
    order = []
    for i in range(repeat):
        order += ['up', 'down'] if i % 2 == 0 else ['down', 'up']
    return order


def iperf_argv(args, direction):
    # "up" means macOS sends to the Linux side: the Linux-side client reverses.
    argv = shlex.split(args.exec_prefix or '') + [
        'iperf3', '-c', args.peer, '-J', '-t', str(args.time), '-O', str(args.omit),
        '-P', str(args.streams), '-p', str(args.port)]
    if args.bind:
        argv += ['-B', args.bind]
    if direction == 'up':
        argv.append('-R')
    return argv


def parse_iperf(data):
    end = data.get('end', {})
    received = end.get('sum_received', {})
    sent = end.get('sum_sent', {})
    return {
        'gbit_s': received.get('bits_per_second', 0) / 1e9,
        'bytes': received.get('bytes', 0),
        'retransmits': sent.get('retransmits', ''),
    }


def delta(before, after, key):
    return after.get(key, 0) - before.get(key, 0)


def make_row(args, ctx, direction, sample, result, before, after, alerts):
    gib = result['bytes'] / 2**30
    ticks = delta(before, after, 'softirq_ticks') / os.sysconf('SC_CLK_TCK')
    moved = delta(before, after, 'rx_bytes' if direction == 'up' else 'tx_bytes')
    # Interface bytes include headers and omitted seconds, so they must exceed
    # the payload when the traffic really crossed this interface.
    verified = result['bytes'] > 0 and moved >= 0.95 * result['bytes']
    return {
        'phase': args.phase, 'direction': direction, 'sample': sample,
        'receiver_gbit_s': f"{result['gbit_s']:.6f}",
        'tcp_retransmits': result['retransmits'],
        'host_softirq_seconds_per_gib': f'{ticks / gib:.6f}' if gib else '',
        'rx_errors': delta(before, after, 'rx_errors'),
        'rx_dropped': delta(before, after, 'rx_dropped'),
        'bad_checksums': delta(before, after, 'rx_bad_checksum'),
        'invalid_packets': delta(before, after, 'rx_invalid_packets'),
        'tx_errors': delta(before, after, 'tx_errors'),
        'tx_dropped': delta(before, after, 'tx_dropped'),
        'softnet_dropped': delta(before, after, 'softnet_dropped'),
        'rx_normalized_packets': delta(before, after, 'rx_normalized_packets'),
        'transport_verified': 'yes' if verified else 'NO',
        'kernel_alerts': 'unchecked' if alerts is None else len(alerts),
        **ctx,
    }

# --- subcommands -----------------------------------------------------------


def environment(iface, peer=''):
    mod = module_info()
    master = master_of(iface)
    lines = [
        f'date: {datetime.date.today().isoformat()}',
        f'kernel: {os.uname().release}',
        f'os: {os_name()}',
        f'cpus: {os.cpu_count()}',
        f"module: version={mod['version']} srcversion={mod['srcversion']}",
        f"module params: {mod['params'] or '-'}",
        f"module file: {mod['path']}",
        f"dkms: {cmd(['dkms', 'status', '-m', 'thunderbolt-net']) or '-'}",
        f'interface: {iface} operstate={read(f"/sys/class/net/{iface}/operstate")} '
        f'mtu={read(f"/sys/class/net/{iface}/mtu")} master={master or "-"}',
        f'default route via: {default_route_dev() or "?"}',
        f"ip_forward: {read('/proc/sys/net/ipv4/ip_forward', '?')}",
    ]
    for q in sorted(pathlib.Path('/sys/class/net', iface, 'queues').glob('rx-*')):
        lines.append(f'{q.name} rps_cpus: {read(q / "rps_cpus")}')
    offloads = cmd(['ethtool', '-k', iface])
    wanted = ('tcp-segmentation-offload', 'generic-segmentation-offload',
              'generic-receive-offload', 'rx-checksumming', 'tx-checksumming',
              'scatter-gather', 'rx-gro-hw', 'rx-gro-list')
    lines.append('offloads: ' + ', '.join(
        l.strip() for l in offloads.splitlines() if l.strip().split(':')[0] in wanted))
    lines.append('driver stats: ' + ', '.join(
        f'{k}={v}' for k, v in parse_ethtool_stats(cmd(['ethtool', '-S', iface])).items()))
    for dev in sorted(pathlib.Path('/sys/bus/thunderbolt/devices').glob('*')):
        name = read(dev / 'device_name')
        if name:
            attrs = ' '.join(f'{a}={read(dev / a)}' for a in
                             ('generation', 'rx_speed', 'rx_lanes', 'tx_speed', 'tx_lanes')
                             if (dev / a).exists())
            lines.append(f"thunderbolt {dev.name}: {read(dev / 'vendor_name')} {name} {attrs}")
    return redact('\n'.join(lines), [(peer, '<peer>'), (socket.gethostname(), '<host>')])


def cmd_info(args):
    print(environment(pick_iface(args.iface), args.peer or ''))
    print('\nAlso note on the Mac: macOS version, `sysctl net.inet.tcp.tso`, '
          'MTU and the Thunderbolt Bridge link speed.')


def cmd_reload(args):
    if os.geteuid() != 0:
        sys.exit('reload needs root')
    iface = pick_iface(args.iface)
    master, mtu = master_of(iface), read(f'/sys/class/net/{iface}/mtu')
    route = default_route_dev()
    if route == iface and not args.force:
        sys.exit(f'default route uses {route}; reloading would cut this session. '
                 'Use another uplink or pass --force.')
    if route and route == master:
        print(f'warning: default route uses bridge {master}; make sure its uplink '
              f'is another port, not {iface}')
    params = []
    for p in args.param:
        if not re.fullmatch(r'[a-z_0-9]+=[A-Za-z0-9_]+', p):
            sys.exit(f'invalid parameter {p!r}')
        params.append(p)
    log = KernelLog()
    print(f'unloading {MODULE} ...', flush=True)
    subprocess.run(['modprobe', '-r', MODULE], check=True, timeout=60)
    print(f"loading {MODULE} {' '.join(params)}", flush=True)
    subprocess.run(['modprobe', MODULE] + params, check=True, timeout=60)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if read(f'/sys/class/net/{iface}/operstate') in ('up', 'unknown'):
            break
        time.sleep(0.5)
    else:
        sys.exit(f'{iface} did not come up within {args.timeout}s')
    if mtu and read(f'/sys/class/net/{iface}/mtu') != mtu:
        subprocess.run(['ip', 'link', 'set', iface, 'mtu', mtu], check=True)
        print(f'restored mtu {mtu}')
    if master and master_of(iface) != master:
        subprocess.run(['ip', 'link', 'set', iface, 'master', master, 'up'], check=True)
        print(f're-attached {iface} to {master}')
    if args.peer:
        while time.monotonic() < deadline:
            if subprocess.run(['ping', '-c', '1', '-W', '1', args.peer],
                              capture_output=True).returncode == 0:
                print('peer reachable')
                break
            time.sleep(0.5)
        else:
            sys.exit('peer did not answer ping before the timeout')
    mod = module_info()
    print(f"loaded version={mod['version']} srcversion={mod['srcversion']} "
          f"params: {mod['params']}")
    alerts = [l for l in (log.drain() or []) if ALERT.search(l)]
    if alerts:
        print('KERNEL ALERTS during reload:\n' + redact('\n'.join(alerts)))
        sys.exit(1)


def cmd_run(args):
    if not shutil.which('iperf3') and not args.exec_prefix:
        sys.exit('iperf3 is not installed (apt install iperf3)')
    iface = pick_iface(args.iface)
    out = pathlib.Path(args.output)
    (out / 'raw').mkdir(parents=True, exist_ok=True)
    mod = module_info()
    if mod['version'] == 'not loaded':
        sys.exit(f'{MODULE} is not loaded')
    ctx = {'module_version': mod['version'], 'srcversion': mod['srcversion'],
           'params': mod['params'], 'kernel': os.uname().release,
           'mtu': read(f'/sys/class/net/{iface}/mtu'), 'streams': args.streams,
           'seconds': args.time}
    safe_phase = re.sub(r'[^A-Za-z0-9_.-]', '_', args.phase)
    (out / f'env-{safe_phase}.txt').write_text(environment(iface, args.peer) + '\n')
    results = out / 'results.csv'
    new = not results.exists()
    log = KernelLog()
    if log.fd is None:
        print('warning: /dev/kmsg unreadable; kernel log not checked (run as root)')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    with results.open('a', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if new:
            writer.writeheader()
        samples = {'up': 0, 'down': 0}
        for direction in run_order(args.repeat):
            samples[direction] += 1
            before = snapshot(iface)
            log.drain()
            argv = iperf_argv(args, direction)
            try:
                p = subprocess.run(argv, capture_output=True, text=True,
                                   timeout=args.time + args.omit + 60)
            except subprocess.TimeoutExpired:
                sys.exit(f'iperf3 {direction} timed out; stopping')
            after = snapshot(iface)
            raw = out / 'raw' / f'{stamp}-{safe_phase}-{direction}{samples[direction]}.json'
            raw.write_text(p.stdout)
            try:
                data = json.loads(p.stdout)
            except ValueError:
                data = {'error': p.stderr.strip() or 'no JSON output'}
            if 'error' in data:
                sys.exit(f"iperf3 {direction} failed: {redact(str(data['error']), [(args.peer, '<peer>')])}")
            alerts = log.drain()
            row = make_row(args, ctx, direction, samples[direction],
                           parse_iperf(data), before, after,
                           None if alerts is None else [l for l in alerts if ALERT.search(l)])
            writer.writerow(row)
            fh.flush()
            print(f"{args.phase} {direction}#{samples[direction]}: "
                  f"{float(row['receiver_gbit_s']):.2f} Gbit/s retrans={row['tcp_retransmits']} "
                  f"verified={row['transport_verified']} alerts={row['kernel_alerts']}", flush=True)
            if alerts:
                relevant = [l for l in alerts if ALERT.search(l) or RELEVANT.search(l)]
                if relevant:
                    with (out / f'kernel-{safe_phase}.log').open('a') as k:
                        k.write(redact('\n'.join(relevant)) + '\n')
            if row['kernel_alerts'] not in (0, 'unchecked'):
                sys.exit('kernel alert logged; stopping (see kernel-*.log)')
            time.sleep(args.pause)
    print(f'\nappended to {results}; run `summary --output {out}` to report')


def summarize(rows, baseline=None):
    phases = list(dict.fromkeys(r['phase'] for r in rows))
    baseline = baseline or (phases[0] if phases else None)
    med = {}
    lines = ['| Phase | Dir | n | Median Gbit/s | Min–max | Retrans (max) | '
             'Δ vs baseline | Errors/drops | Verified | Alerts |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |']
    for phase in phases:
        for direction in ('up', 'down'):
            sel = [r for r in rows if r['phase'] == phase and r['direction'] == direction]
            if not sel:
                continue
            rates = [float(r['receiver_gbit_s']) for r in sel]
            med[phase, direction] = statistics.median(rates)
            retrans = [int(r['tcp_retransmits']) for r in sel if str(r['tcp_retransmits']).isdigit()]
            bad = sum(int(r[k]) for r in sel for k in
                      ('rx_errors', 'rx_dropped', 'bad_checksums', 'invalid_packets',
                       'tx_errors', 'tx_dropped', 'softnet_dropped'))
            alerts = [r['kernel_alerts'] for r in sel]
            ref = med.get((baseline, direction))
            change = ('—' if phase == baseline or not ref
                      else f'{(med[phase, direction] / ref - 1) * 100:+.1f}%')
            lines.append(
                f'| {phase} | {direction} | {len(sel)} | {med[phase, direction]:.2f} | '
                f'{min(rates):.2f}–{max(rates):.2f} | '
                f"{max(retrans) if retrans else '?'} | {change} | {bad} | "
                f"{'all' if all(r['transport_verified'] == 'yes' for r in sel) else 'NO'} | "
                f"{'unchecked' if 'unchecked' in alerts else sum(int(a) for a in alerts)} |")
    builds = ['', 'Builds:']
    for phase in phases:
        r = next(r for r in rows if r['phase'] == phase)
        builds.append(f"- {phase}: version {r['module_version']} srcversion {r['srcversion']}; "
                      f"{r['params'] or 'no params'}; kernel {r['kernel']}; mtu {r['mtu']}; "
                      f"{r['streams']} stream(s) × {r['seconds']} s")
    return '\n'.join(lines + builds)


def cmd_summary(args):
    out = pathlib.Path(args.output)
    with (out / 'results.csv').open() as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit('no results recorded')
    text = ['## Hardware test summary', '',
            '"up" = macOS → Linux, "down" = Linux → macOS. Rates are receiver medians.', '',
            summarize(rows, args.baseline)]
    for env in sorted(out.glob('env-*.txt')):
        text += ['', f'### Environment ({env.stem[4:]})', '```', env.read_text().strip(), '```']
    for klog in sorted(out.glob('kernel-*.log')):
        text += ['', f'### Kernel log excerpt ({klog.stem[7:]})', '```',
                 klog.read_text().strip()[-4000:], '```']
    report = redact('\n'.join(text), [(socket.gethostname(), '<host>')])
    print(report)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from privacy import findings
    leaks = findings(report)
    if leaks:
        print('\nwarning: review before sharing; possible ' + ', '.join(leaks), file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    default_out = f'hw-results-{datetime.date.today().isoformat()}'

    p = sub.add_parser('info', help='print a redacted environment report')
    p.add_argument('--iface', help='Thunderbolt netdev (auto-detected)')
    p.add_argument('--peer', help='peer address to redact')
    p.set_defaults(func=cmd_info)

    p = sub.add_parser('reload', help='reload the module with parameters (root)')
    p.add_argument('--iface')
    p.add_argument('--peer', help='ping this address after reload')
    p.add_argument('--param', action='append', default=[], metavar='NAME=VALUE',
                   help='e.g. rx_page_pool=0; repeatable. Overrides modprobe.d; an omitted '
                        'parameter takes its modprobe.d value (package defaults), not the '
                        'module built-in default')
    p.add_argument('--timeout', type=int, default=60)
    p.add_argument('--force', action='store_true',
                   help='reload even if the default route uses the interface')
    p.set_defaults(func=cmd_reload)

    p = sub.add_parser('run', help='alternating iperf3 uploads/downloads for one phase')
    p.add_argument('--phase', required=True, help='label, e.g. v0.2.0 or candidate-pool-on')
    p.add_argument('--peer', required=True, help='iperf3 server address on the Mac')
    p.add_argument('--iface')
    p.add_argument('--repeat', type=int, default=3, help='runs per direction')
    p.add_argument('--time', type=int, default=10)
    p.add_argument('--omit', type=int, default=1)
    p.add_argument('--streams', type=int, default=1)
    p.add_argument('--port', type=int, default=5201)
    p.add_argument('--pause', type=float, default=2.0)
    p.add_argument('--bind', help='local address for iperf3 -B')
    p.add_argument('--exec-prefix', help='run iperf3 elsewhere behind the bridge, '
                   'e.g. "pct exec 101 --" or "ip netns exec test"')
    p.add_argument('--output', default=default_out)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('summary', help='print a redacted report to share')
    p.add_argument('--output', default=default_out)
    p.add_argument('--baseline', help='phase to compare against (default: first)')
    p.set_defaults(func=cmd_summary)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
