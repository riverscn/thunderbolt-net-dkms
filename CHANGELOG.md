# Changelog

## 0.2.0 — unreleased

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
