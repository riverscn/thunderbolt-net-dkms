# Changelog

## 0.4.0

- Learn each flow's path MTU (`rx_segment_learn`, default on, experimental):
  the SYN/SYN-ACK MSS option and the ICMP Fragmentation Needed / Packet Too
  Big that leave toward the peer bound each flow and destination, so a
  normalized aggregate is split at the learned limit instead of
  `rx_segment_mtu`, never above the interface MTU. A bound below the fallback
  also lowers the size above which packets are examined. Flows without a
  record keep the fallback. Counters `rx_bound_learned_mss`,
  `rx_bound_learned_icmp`, `rx_bound_hits`.
- Describe `rx_segment_mtu` as the fallback path MTU; the parameter name is
  unchanged.
- Add real TCP sessions through a metadata-loss link to the QEMU suite,
  including an 802.1Q case; build `CONFIG_VLAN_8021Q` into the pinned upstream
  test kernel. Hardware runs on a PPPoE uplink and the local Thunderbolt link
  are recorded in `docs/path-mtu.md`.

## 0.3.1

- Change the packaged and built-in `rx_segment_mtu` default from 1500 to 1280.
  The cap is the IP length of segments rebuilt from a peer's TCP aggregates and
  must not exceed the smallest path MTU beyond the host. Behind a PPPoE uplink
  (MTU 1492), 1500 reduced macOS uploads through the host to 0.1 Mbit/s; 1280
  restored the uplink's ceiling. See `docs/path-mtu.md`.
- Document the cap's semantics, the measurement, common path limits and the
  symptom of a value that is too large. An existing
  `/etc/modprobe.d/thunderbolt-net.conf` keeps its own value.
- Check the built-in default in the QEMU parameter test. No other driver change.

## 0.3.0

- Rebase the driver on Linux 7.2.9. Upstream imports keep their original
  authorship; they include TX end-to-end flow-control removal, complete-packet
  RX statistics, the fragment-count bound and service-driver ring throttling.
- Request the previous 128-microsecond interrupt throttling through
  `tb_ring_throttling()` when the target kernel provides it; older cores keep
  their own moderation. Use the core frame-size helper when available.
- Retain oversized TCP RX normalization, RX page recycling, lifecycle
  serialization and the GRO header-length correction unchanged in behavior.
- Enable them by default: the package installs one modprobe file,
  `/usr/lib/modprobe.d/thunderbolt-net.conf`, with
  `rx_segment=1 rx_segment_mtu=1500 rx_page_pool=1`. It replaces the two opt-in
  examples, is removed with the package, and can be overridden by
  `/etc/modprobe.d/thunderbolt-net.conf`. Module built-in defaults stay off.
- Allow Linux 6.8–6.19 and 7.0–7.2 in the DKMS build gate. Add checksum-pinned
  upstream-kernel and Arch Linux kernel CI, and assert the throttling path of
  every built module.
- Add a manual hardware test helper and record regression results: throughput
  matched 0.2.0 on Linux 7.0, and on Linux 7.2.9 the throttling call avoided an
  11–44× increase in interrupts. See `docs/upstream-validation.md`.

## 0.2.0 — experimental

- Add opt-in DMA-mapped RX page recycling (`rx_page_pool=1`), disabled by
  default and independent of oversized TCP normalization (`rx_segment`).
- Use NAPI SKB allocation; finish ring priming and DMA-path setup before NAPI
  activation, and drain polling before releasing RX resources.
- Handle zero-budget polling, interrupt rearming and partial setup failures.
- Backport upstream DMA-path teardown, Rx HopID release and login-state fixes.
- Add guest-only RX lifecycle regression tests and DMA synchronization controls
  to CI. See `docs/validation.md` for the current hardware results.

## 0.1.1 — experimental

- Keep the Ethernet header length at `ETH_HLEN` and reserve Thunderbolt
  transport-header space through `needed_headroom`. This prevents GRO from
  comparing IP-header bytes as part of the link-layer header and reordering
  differently sized packets belonging to the same TCP flow.
- Add 256 multi-packet GRO ordering cases and a legacy-header negative control
  to the existing isolated QEMU tests used by CI.
- Document the independent scope of this fix and limited hardware A/B/A results.

The GRO correction is active with either `rx_segment` setting. Oversized TCP RX
normalization remains opt-in. This release does not claim to eliminate all TCP
retransmissions, and long-duration stability and hotplug validation remain open.

## 0.1.0 — experimental

- Package the validated RX GSO normalization prototype as a source-only DKMS
  replacement for `thunderbolt_net`, with explicit opt-in and rollback guidance.
- Preserve aggregates for local delivery while providing conservative segment
  metadata for supported forwarding paths.
- Include the separately attributed upstream fragment-count bounds correction.
- Add packet and forwarding tests, Debian package lifecycle checks, and CI.
- Export public source from an explicit allowlist, with privacy checks and
  anonymized hardware observations.

Known limitations include unsupported TCP/IP extensions, incomplete physical
bridge and long-duration validation, and independent hot-reload/power-management
issues. Read the design and compatibility documents before installation.
