# Testing and evidence

## Source and packaging checks

```sh
make check
dpkg-buildpackage --build=binary --no-sign
python3 scripts/audit-deb.py ../thunderbolt-net-dkms_0.1.0-1_all.deb
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
- Candidate module load/unload and explicit parameter activation.

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

## Remaining validation

Physical two-port bridge throughput, long-duration soak, KASAN, allocation-fault
injection, real MTU 9000 forwarding and broader peer/kernel combinations remain
unvalidated. One development hot reload required restarting the network interface
before negotiation recovered. That behavior is not claimed fixed by this patch.

For hardware reports, compare stock and patched under identical conditions,
measure both directions, record errors and retransmissions, and verify payload
integrity. Use an independent management connection and remove private details
before sharing results.
