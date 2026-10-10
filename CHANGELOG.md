# Changelog

## 0.3.0 — unreleased

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
  upstream-kernel CI and assert the throttling path of every built module.
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
