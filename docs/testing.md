# Testing and evidence

## Source and packaging checks

```sh
make check
dpkg-buildpackage --build=binary --no-sign
python3 scripts/audit-deb.py ../thunderbolt-net-dkms_0.1.1-1_all.deb
```

Source checks verify the release allowlist, source provenance hashes, version
consistency, privacy patterns and pinned GitHub Actions. Unit tests exercise
privacy rejection and deterministic source exports. The Debian audit checks
source-only payload paths and maintainer scripts.

`scripts/ci-linux.sh` is intended for disposable containers. It installs build
dependencies, selects an installed test kernel, builds the package, runs QEMU,
and tests actual Debian install/removal plus restoration of the original module.
Do not run it on a production host or bind-mount host module/DKMS directories.

Before the 0.1.0 release, this same CI script passed in three isolated x86-64
containers. These are local reproductions of the workflow, not GitHub-hosted
run results:

| Distribution | Kernel tested | Result |
| --- | --- | --- |
| Ubuntu 24.04 | `6.8.0-146-generic` | Build, QEMU and DKMS lifecycle passed |
| Debian 13 | `6.12.111+deb13-amd64` | Build, QEMU and DKMS lifecycle passed |
| Ubuntu 26.04 | `7.0.0-38-generic` | Build, QEMU and DKMS lifecycle passed |

Each run passed all 27 packet cases and 16 forwarding-path cases. Package removal
restored selection of the original distribution module and its SHA-256 matched
the value recorded before installation. Lintian reported no errors; the initial
changelog does not reference a Debian ITP bug because this is an independent
project, not a submission to the Debian archive.

## Kernel tests without hardware

With matching kernel image, headers, bridge and Thunderbolt modules installed:

```sh
python3 scripts/qemu-test.py --kernel TEST_KERNEL_RELEASE
```

Replace `TEST_KERNEL_RELEASE` with the installed test kernel release. Additional
tools: `qemu-system-x86 busybox-static iproute2 kmod cpio zstd xz-utils`.
The runner creates a temporary initramfs and boots one virtual CPU with 1 GiB RAM
under TCG. It provides no disk, host mounts, passed-through controller or external
network interface. Test modules load only inside that guest.

Assertions cover:

- 27 packet tests: checksums, bytes, TCP sequence/flags/options, IPv4/IPv6,
  VLAN/QinQ, cloned and nonlinear SKBs, MTU boundaries and malformed inputs.
- 16 paths: bridge/router × IPv4/IPv6 × original/normalized × direct/GRO.
  Output is captured after the real kernel forwarding/segmentation path and
  validated by independent byte-oriented checksum logic.
- 256 multi-packet ordering cases: eight size patterns × original/normalized ×
  IPv4/IPv6 × linear/paged SKBs × timestamps on/off × PSH variation. Capture
  after real NAPI/GRO checks delivery order, byte coverage, payload integrity
  and sequence wraparound. A second run emulates the old 26-byte header length
  and must reproduce reordering without loss or corruption. The runner does
  not require an identical negative-control count on every kernel.
- Candidate module load/unload and explicit parameter activation.

The ordering test emulates the net-device header length/headroom settings in
`tbnet_probe()`; it does not execute the physical device probe or Thunderbolt
DMA path. Keep those settings in sync when changing the driver. The full
container CI pipeline was rerun for this addition on `6.8.0-146-generic`,
`6.12.111+deb13-amd64`, and `7.0.0-38-generic`, including package installation,
removal and original-module restoration. On each kernel, the corrected 14-byte
header/12-byte headroom passed all 256 cases, while the legacy layout reordered
120 of 256 cases without loss or corruption. These are local container results,
not GitHub-hosted runs. Test modules are only built with `TBNET_BUILD_TESTS=1`
and are not installed by the DKMS package.

Private addresses formerly used during development were replaced with RFC 5737
and RFC 3849 documentation addresses, including integer-encoded test fixtures.
QEMU logs are local build outputs and are excluded from public release artifacts.

## Anonymized hardware observations

One x86-64 Linux 7.0 host received traffic from a macOS peer with TSO enabled.
Both Thunderbolt interfaces used MTU 1500. Three alternating stock/patched rounds
ran four native iperf3 cases for ten seconds each: 24 successful measurements.
The client path was bound to and checked against the Thunderbolt interface.

| Receiver throughput, Gbps (median) | Stock | Patched |
| --- | ---: | ---: |
| IPv4 upload, one stream | 24.18 | 24.44 |
| IPv4 upload, four streams | 24.24 | 24.31 |
| IPv4 download, one stream | 27.98 | 27.51 |
| IPv6 upload, one stream | 21.54 | 22.92 |

IPv4 differences were within 2%. Whole-VM upload CPU cost did not increase in
these samples; download CPU seconds per interface GB had a 3.6% higher median.
CPU measurements include background activity, and scheduling/frequency were not
fixed. These small samples do not prove statistical equivalence or a universal
speedup. The packaged source adds version metadata; hardware figures refer to
the validated normalization code before packaging, not a new package benchmark.

A separate six-second test through real IPv4 Docker DNAT, bridge and veth reached
23.35 Gbps upload and 27.86 Gbps download. A random 32 MiB payload's byte length and
SHA-256 matched at the receiver. Reverse forwarding had substantial TCP
retransmissions; their cause was not isolated. Native measurement windows had no
new driver RX errors/drops or softnet drops, which does not imply zero TCP retries.

The original driver stalled on the tested container HTTP upload while local
delivery worked. The patched path completed it. Raw packets, host identifiers,
local paths, addresses and private diagnostic logs are intentionally not shipped.
The anonymous summary is limited evidence, not independently reproducible raw data.

### GRO header-length correction (0.1.1)

A separate short A/B/A test compared version 0.1.0, the header-length correction,
and restored version 0.1.0 on one x86-64 Linux 7.0 host. All three phases kept
macOS TSO enabled, MTU 1500, GRO and RX normalization enabled, and the same IRQ
affinity. Each phase used three ten-second single-stream IPv4 uploads to the
host and three to a VirtIO guest behind its Linux bridge. One download per
destination per phase brought the total to 24 successful iperf3 measurements.

| Upload metric | Before | Corrected | Restored |
| --- | ---: | ---: | ---: |
| Native receiver throughput median, Gbps | 24.13 | 24.22 | 23.49 |
| Bridged guest throughput median, Gbps | 22.75 | 23.34 | 22.74 |
| Guest `TCPOFOQueue` increment, three runs combined | 112,964 | 3 | 136,479 |
| Guest iperf3 receiver CPU median, percent of one logical CPU | 52.5 | 44.1 | 49.2 |

The guest upload median improved by about 2.6%; native upload remained near
24 Gbps. The disappearance and return of large out-of-order queue increments
agree with the software-path regression. No new driver RX errors/drops or host
softnet drops were observed during these tests.

One corrected native upload still reported 2,098 retransmissions alongside
zero-window events. Their causal relationship was not isolated. Background
workloads continued; TCP counters cover a network namespace, not a filtered
flow, and CPU percentages include scheduling effects. This short, non-randomized
sequence is not a soak test or proof of a universal speedup. The single download
sample per phase is insufficient for a download-performance claim. IPv6
throughput and physical unplug/reboot behavior of this correction were not
tested. The original module and settings were restored afterward.

## Remaining validation

Physical two-port bridge throughput, long-duration soak, KASAN, allocation-fault
injection, real MTU 9000 forwarding and broader peer/kernel combinations remain
unvalidated. One development hot reload required restarting the network interface
before negotiation recovered. That behavior is not claimed fixed by this patch.

For hardware reports, compare stock and patched under identical conditions,
measure both directions, record errors and retransmissions, and verify payload
integrity. Use an independent management connection and remove private details
before sharing results.
