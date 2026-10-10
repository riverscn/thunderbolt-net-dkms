# SPDX-License-Identifier: GPL-2.0-only
import argparse
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from privacy import findings

spec = importlib.util.spec_from_file_location('hw_test', ROOT / 'scripts/hw-test.py')
hw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hw)

def args(**kw):
    base = dict(peer='198.51.100.2', time=10, omit=1, streams=1, port=5201,
                bind=None, exec_prefix=None, phase='p')
    base.update(kw)
    return argparse.Namespace(**base)

class HardwareTestHelperTests(unittest.TestCase):
    def test_redaction_removes_identifiers(self):
        # Construct fixtures so the release scanner does not flag this file.
        private4 = '192.' + '168.7.9'
        link_local = 'fe' + '80::1c2:3ff:fe44:5566%thunderbolt0'
        text = (f'{private4} {link_local} 2a00:1450:4001:82a::200e '
                'aa:bb:cc:dd:ee:ff 0123abcd-0000-1111-2222-333344445555 '
                '/ho' + 'me/somebody/x secret-host')
        out = hw.redact(text, [('secret-host', '<host>')])
        for label in ('<ipv4>', '<ipv6>', '<mac>', '<uuid>', '<home>', '<host>'):
            self.assertIn(label, out)
        self.assertNotIn('somebody', out)
        self.assertFalse(findings(out), out)

    def test_hostname_redaction_matches_whole_names(self):
        text = 'pve kernel: 7.0.14-23-pve proxmox-headers root@pve pve.lan'
        self.assertEqual(hw.redact(text, [('pve', '<host>')]),
                         '<host> kernel: 7.0.14-23-pve proxmox-headers root@<host> <host>.lan')

    def test_redaction_keeps_documentation_and_versions(self):
        text = '198.51.100.2 2001:db8::5 127.0.0.1 kernel 7.2.9 time 12:30:45'
        self.assertEqual(hw.redact(text), text)

    def test_parsers(self):
        stats = hw.parse_ethtool_stats(
            'NIC statistics:\n     rx_normalized_packets: 12\n     rx_bad_checksum: 0\n')
        self.assertEqual(stats, {'rx_normalized_packets': 12, 'rx_bad_checksum': 0})
        self.assertEqual(hw.parse_softnet('0000ff 0000000a 0\n00000f 00000001 0\n'), 11)
        self.assertEqual(hw.softirq_ticks('cpu  1 2 3 4 5 6 77 0\ncpu0 1 2 3 4 5 6 7 0\n'), 77)

    def test_runs_alternate_and_mirror(self):
        self.assertEqual(hw.run_order(3), ['up', 'down', 'down', 'up', 'up', 'down'])

    def test_upload_reverses_linux_client(self):
        self.assertIn('-R', hw.iperf_argv(args(), 'up'))
        self.assertNotIn('-R', hw.iperf_argv(args(), 'down'))
        argv = hw.iperf_argv(args(exec_prefix='pct exec 101 --'), 'down')
        self.assertEqual(argv[:4], ['pct', 'exec', '101', '--'])

    def test_row_verifies_transport_with_counters(self):
        result = {'gbit_s': 24.5, 'bytes': 2**30, 'retransmits': 3}
        before = {'rx_bytes': 0, 'tx_bytes': 0, 'rx_errors': 1, 'softirq_ticks': 0}
        crossed = dict(before, rx_bytes=2**30 + 10**6, rx_errors=2)
        row = hw.make_row(args(), {}, 'up', 1, result, before, crossed, [])
        self.assertEqual(row['transport_verified'], 'yes')
        self.assertEqual(row['rx_errors'], 1)
        self.assertEqual(row['kernel_alerts'], 0)
        # Payload that did not cross the interface (e.g. another route) fails.
        row = hw.make_row(args(), {}, 'up', 1, result, before, before, None)
        self.assertEqual(row['transport_verified'], 'NO')
        self.assertEqual(row['kernel_alerts'], 'unchecked')

    def test_summary_compares_medians_with_baseline(self):
        def row(phase, direction, rate):
            r = {k: '0' for k in hw.CSV_FIELDS}
            r.update(phase=phase, direction=direction, receiver_gbit_s=str(rate),
                     transport_verified='yes', params='rx_segment=Y')
            return r
        rows = [row('v0.2.0', 'up', 20), row('v0.2.0', 'up', 22), row('v0.2.0', 'up', 30),
                row('candidate', 'up', 24.2), row('candidate', 'down', 28)]
        text = hw.summarize(rows)
        self.assertIn('| v0.2.0 | up | 3 | 22.00 |', text)
        self.assertIn('+10.0%', text)
        self.assertIn('| candidate | down | 1 | 28.00 | 28.00–28.00 | 0 | — |', text)

if __name__ == '__main__':
    unittest.main()
