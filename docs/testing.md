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

## Hardware test helper

`scripts/hw-test.py` automates the throughput part of this procedure on the
Linux host (Python 3.9+, `iperf3`, `ethtool`, `iproute2`; run as root so the
kernel log can be checked). It does not install packages or switch DKMS
versions; install the build under test first. Keep the host's default route
off the Thunderbolt interface: reloading the module drops that link.

On the Mac, find the Thunderbolt Bridge address (usually
`ipconfig getifaddr bridge0`) and start `iperf3 -s`. On the Linux host, set
that address once in the shell, then run the steps in the same shell:

```sh
PEER=198.51.100.2   # replace with the Mac's Thunderbolt Bridge IP address
sudo python3 scripts/hw-test.py info --peer "$PEER"
sudo python3 scripts/hw-test.py reload --peer "$PEER" --param rx_segment=1
sudo python3 scripts/hw-test.py run --peer "$PEER" --phase v0.2.0
# install the candidate package, then repeat with a new phase label
sudo python3 scripts/hw-test.py run --peer "$PEER" --phase candidate
sudo python3 scripts/hw-test.py summary
```

`run` alternates three uploads (macOS → Linux) and three downloads, mirrored
between pairs, with 10-second measurements after one omitted second. Each run
records receiver rate, retransmissions, interface and driver counter deltas,
softnet drops and host softirq time per GiB, and checks that the interface byte
counters cover the payload (`transport_verified`). It stops on a kernel warning,
Oops or similar log entry, or an iperf3 timeout. Repeat the baseline phase after
the candidate to bracket it. `reload` restores the interface MTU and bridge
membership and waits for the peer; it refuses to run while the default route
uses the interface unless `--force` is given.

To measure bridge forwarding, run the client behind the bridge with
`--exec-prefix`, for example `--exec-prefix "pct exec 101 --"` for a Proxmox
container or `--exec-prefix "ip netns exec test"`. The counters are still
read from the host's Thunderbolt interface.

Results accumulate in `hw-results-DATE/` (`--output` to change). `results.csv`
starts with the columns of [iperf3-results.csv](iperf3-results.csv). `summary`
prints per-phase medians and changes against the first phase (`--baseline`),
build identities, the environment and kernel log excerpts, with addresses,
MAC addresses, UUIDs, home paths and the hostname replaced by labels. Review
it before sharing; keep the `raw/` iperf3 JSON private. Mac-side details
(macOS version, `sysctl net.inet.tcp.tso`, link speed) must be added by hand.
Reconnection and physical disconnect checks remain manual.

### Reporting results

Share these as text, in an issue, pull request or review discussion:

1. The `summary` output, for example from
   `sudo python3 scripts/hw-test.py summary > summary.md`.
2. `results.csv` from the same output directory. It holds the per-run numbers
   needed to update [iperf3-results.csv](iperf3-results.csv) and
   [validation](validation.md); it contains labels, counters, `srcversion`,
   module parameters and the kernel release, but no addresses.
3. The details the helper cannot collect:

   ```text
   macOS version:
   sysctl net.inet.tcp.tso:
   Thunderbolt Bridge link speed and MTU:
   Topology: Linux host directly, or bridged to a VM/container (--exec-prefix)
   Anything unusual: disconnects, stalls, manual intervention
   ```

If `summary` ends with `warning: review before sharing`, the privacy scan
matched something; inspect and remove it first. Never attach `raw/`, full
kernel logs or an unreviewed support bundle (see [privacy](privacy.md)).
Phases with the same `version` are distinguished by `srcversion`.
