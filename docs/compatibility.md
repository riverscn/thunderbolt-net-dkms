# Compatibility

Build, software-path tests, and hardware interoperability are separate claims.
A successful compile does not establish cable, firmware or macOS compatibility.

Version 0.3.0 uses the Linux v7.2.9 driver sources; the published 0.2.0 used
the v7.0 baseline with three connection-cleanup backports. The
[current validation](upstream-validation.md) distinguishes hardware results
from builds and guest-only tests.

| Target | Validation path |
| --- | --- |
| Ubuntu 24.04 | CI build, QEMU and DKMS lifecycle |
| Debian 13 | CI build, QEMU and DKMS lifecycle |
| Ubuntu 26.04 | CI build, QEMU, RX lifecycle model and DKMS lifecycle |
| Proxmox 7.0.14-23-pve | Manual hardware validation with a macOS peer |
| Upstream Linux 7.2.9 | Pinned source build, QEMU, DKMS lifecycle and throttling-path CI |
| Proxmox 7.0.14-23-pve | Manual hardware regression against v0.2.0 (older-core fallback) |
| Arch Linux 7.2.9-arch1-1 | Manual build and passed-through controller test (new-core API path) |
| Current Arch packages / mainline RC | Not validated as distribution packages |

The build gate permits x86-64 Linux 6.8–6.19 or 7.0–7.2. This is an allowed
development range, not a promise that every intermediate/vendor kernel works.
Exact versions selected by distribution repositories are printed by CI. Future
ABI changes require review; unsupported versions are skipped by DKMS autoinstall
and may use the distribution module instead. Check DKMS status after upgrades.

The source-only Debian package uses `Architecture: all` because its payload is
text. This does not imply the driver is validated on every CPU architecture.
Use matching vendor kernel headers and inspect any build failure rather than
forcing a module built for another ABI to load.

Peer testing covered TSO enabled and MTU 1500. Synthetic tests cover other MTUs
and packet edge cases; those are not substitutes for hardware validation.
PCI passthrough controller wake-up and link negotiation remain outside the RX
normalization change. No runtime-PM workaround is installed by this package.

The baseline and reproducible update procedure are documented in
[upstream maintenance](upstream-tracking.md). The [v0.2.0 report](validation.md)
does not cover this newer baseline; its own results are in the
[upstream validation](upstream-validation.md). New kernel build evidence does not
carry hardware results forward automatically.

No hardware performance improvement over v0.2.0 is claimed for the 7.2.9
baseline. In particular, the new throttling API retains the old 128-microsecond
interval; see [performance expectations and hardware regression](upstream-tracking.md#performance-expectations-and-validation).
The Arch result comes from a VM with the controller passed through, not from a
bare-metal Arch installation or an Arch package.
