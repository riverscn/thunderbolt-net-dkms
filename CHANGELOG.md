# Changelog

## Unreleased

- Keep the Ethernet header length at `ETH_HLEN` and reserve Thunderbolt
  transport-header space through `needed_headroom`. This prevents GRO from
  comparing IP-header bytes as part of the link-layer header and reordering
  differently sized packets belonging to the same TCP flow.
- Add 256 multi-packet GRO ordering cases and a legacy-header negative control
  to the existing isolated QEMU tests used by CI.
- Document the independent scope of this fix and limited hardware A/B/A results.

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
