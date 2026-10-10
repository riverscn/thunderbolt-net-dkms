# Testing

Current results are in [validation](validation.md).

## Source and packaging checks

```sh
make check
dpkg-buildpackage --build=binary --no-sign
python3 scripts/audit-deb.py "../thunderbolt-net-dkms_$(cat VERSION)-1_all.deb"
```

Source checks verify the release allowlist, source provenance hashes, version
consistency, privacy patterns and pinned GitHub Actions. Unit tests exercise
privacy rejection and deterministic source exports. The Debian audit checks
source-only payload paths and maintainer scripts.

`scripts/ci-linux.sh` is intended for disposable containers. It installs build
dependencies, selects an installed test kernel, builds the package, runs QEMU,
and tests actual Debian install/removal plus restoration of the original module.
Do not run it on a production host or bind-mount host module/DKMS directories.

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

The ordering test emulates network-device header settings; it does not execute
physical Thunderbolt probe or DMA. Test modules are excluded from DKMS installs.

## RX lifecycle tests

[tests/rx-lifecycle](../tests/rx-lifecycle/README.md) extracts ten production
functions and runs 63 cases in a diskless guest. It covers both allocators,
startup/stop concurrency, delayed SKB release, partial setup failures and DMA
synchronization controls. The harness substitutes NHI rings and device behavior;
physical hotplug still requires hardware.

## Hardware procedure

Use the same macOS peer, Linux kernel, topology, MTU, offloads and IRQ/RPS
placement for baseline and candidate. Bind iperf3 to the Thunderbolt interface,
check the actual process interface and compare netdev byte counters. Alternate
upload/download, repeat each direction and bracket the baseline with candidate
runs. Keep browser tests separate from the primary throughput result.

For lifecycle checks, record boot identity, kernel logs and netdev counters
before/after each step. Verify traffic before interrupting it and after recovery;
a loaded module alone does not establish a working connection. Stop on a kernel
fault or timeout. Physical disconnect testing requires a cable operator and an
independent console/recovery path. Never load intentional sanitizer controls
onto the hardware host.
