# Validation — 0.2.0

This report covers the current driver on 2026-10-10. It replaces development
notebooks as the performance reference. Throughput uses iperf3, not browser
speed tests. Public per-run results are in [iperf3-results.csv](iperf3-results.csv).

## Hardware throughput

One x86-64 Proxmox `7.0.14-23-pve` host and a macOS peer, forwarding through
Linux Bridge to a VirtIO VM. MTU 1500, macOS TSO enabled, `rx_segment=1`,
identical IRQ/effective CPU placement and RPS. Each single-stream measurement
lasted 10 seconds after one omitted warmup second. Interface binding, process
interface observations and netdev byte counters verified Thunderbolt transport.
No deliberate competing test traffic or interface cycling ran during measurement.

Four sequential phases each contained three uploads and three downloads,
alternating direction (24 completed runs). Rates are receiver medians.

| Phase | Upload, Gbit/s | Download, Gbit/s |
| --- | ---: | ---: |
| 0.2.0, pool on, before | 24.45 | 28.17 |
| 0.1.1 | 23.31 | 27.96 |
| 0.2.0, pool off | 22.73 | 28.76 |
| 0.2.0, pool on, after | 24.43 | 28.31 |
| Pool-on before/after combined | 24.44 | 28.24 |

The combined pool-on sample has six measurements per direction; baseline and
pool-off each have three. Pool-on upload was 4.8% above baseline, with all six
uploads above the three baseline uploads. Download differed by 1.0%. Pool-off
upload was 2.5% below baseline in this block; do not hide that result. The
candidate's shared lifecycle changes are present with either allocator.

No Thunderbolt RX/TX error/drop, invalid-packet, bad-checksum or host/VM softnet
drop increments occurred. TCP retransmissions ranged from 0 to 2,189 per run;
see the CSV. No kernel warning, Oops, panic or reboot occurred in this comparison.
Whole-host softirq counters are provided for context, not driver-exclusive CPU
cost. Background workloads and frequency were not isolated; the phase order was
not randomized. These results establish observations on this setup, not universal
performance or statistical equivalence across machines.

## RX lifecycle and data integrity

Six idle interface down/up cycles completed, three per allocator. Two further
cycles interrupted verified four-stream uploads, one per allocator. All eight
recovered carrier about 1.5 seconds after link-up (0.5-second sampling). The
active cycles verified bulk RX before interruption and after recovery. Neither
allocator produced a new kernel warning, Oops or panic in these trials.

After the cycles, newly generated 64 MiB random payloads were uploaded to the
host and to the bridged VM. Both received lengths and SHA-256 digests matched.
These are sampled integrity checks, not exhaustive corruption detection.

One physical disconnect was performed during verified four-stream upload with
page recycling enabled. The interface was removed, but the peer did not
re-enumerate for at least 326 seconds of observation after disappearance,
including the operator's unplug/replug interval. The host remained reachable,
its boot identity was unchanged, and no new kernel warning/Oops/panic appeared.
The upload did not recover; the test client was then stopped deliberately.
**Physical reconnect acceptance failed in this window.** The test scripts
performed no driver reload or host reboot during this window.

After the operator retried the physical connection, the peer enumerated and
the bridge entered forwarding about one second later. A follow-up check found
the same host boot identity and driver source version, with version 0.2.0 and
page recycling still enabled. The macOS peer reported a 40 Gbit/s link and TSO
enabled. Two fresh single-stream checks (10 seconds plus one omitted warmup
second, bound to Thunderbolt and verified by interface/counter observations)
received 24.47 Gbit/s upload and 28.72 Gbit/s download, with 0 and 2 TCP
retransmissions respectively. RX/TX errors/drops, invalid packets and bad
checksums did not increase. These recovery checks are separate from the
24-run performance comparison above.

**Connectivity and bidirectional transfer recovered after the manual retry.**
The retry timing was not captured, so this does not measure plug-to-ready
latency or establish why the first attempt timed out. The earlier unsuccessful
window remains recorded; reproducible physical reconnection is still an open
validation item and the PR remains draft.

Wired-uplink carrier flaps occurred before and during the disconnected window.
A later log review also found an Ethernet adapter reset, with its TX-timeout
counter at one; the uplink was back at 2.5 Gbit/s at the recovery check. The
observed log order was NIC carrier loss, then bond/bridge-port state changes;
there is no evidence here that a bridge reset caused them. The reconnect
failure occurred before creation of the network interface. Its cause is not
established by this driver test and is not attributed to the allocator.

## Automated validation

`make check` validates source provenance, privacy rules, packaging versions,
deterministic exports, production-function extraction and installation-first
release notes. The final PR checks build packages on Ubuntu 24.04, Debian 13
and Ubuntu 26.04; boot diskless QEMU packet/forwarding/ordering tests; and verify
DKMS install/remove restores the original module. Ubuntu 26.04 also runs the
63-case RX lifecycle model and a missing-synchronization negative control.
The PR check results identify the tested revision; build success is not a
physical Thunderbolt test. See [test commands](testing.md).

## Scope

One controller/peer combination and bounded test windows are covered. Other
controllers, non-coherent platforms, suspend/resume, hours-long stress and
complete hardware DMA leak detection remain outside this report. Kernel
Thunderbolt core, firmware and cable enumeration are not replaced by this
module. Private host identifiers, logs and control scripts are not published.
