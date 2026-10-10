# Compatibility

Build, software-path tests, and hardware interoperability are separate claims.
A successful compile does not establish cable, firmware or macOS compatibility.

The published 0.2.0 driver uses the Linux v7.0 source baseline with three connection
cleanup backports. [Current validation](validation.md) distinguishes hardware
results from builds and guest-only tests.

| Target | Validation path |
| --- | --- |
| Ubuntu 24.04 | CI build, QEMU and DKMS lifecycle |
| Debian 13 | CI build, QEMU and DKMS lifecycle |
| Ubuntu 26.04 | CI build, QEMU, RX lifecycle model and DKMS lifecycle |
| Proxmox 7.0.14-23-pve | Manual hardware validation with a macOS peer |
| Upstream Linux 7.2.9 (development branch) | Pinned source build, QEMU and DKMS lifecycle CI |
| Current Arch packages / mainline RC | Not validated as distribution packages |

This development branch uses Linux 7.2.9 driver sources and permits x86-64,
Linux 6.8–6.19 or 7.0–7.2 in its build gate. This is an allowed
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

The development baseline and reproducible update procedure are documented in
[upstream maintenance](upstream-tracking.md). Hardware results above belong to
v0.2.0, not this newer baseline. New kernel build evidence does not carry those
hardware results forward automatically.
