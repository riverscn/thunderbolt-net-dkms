# Compatibility

Build, software-path tests, and hardware interoperability are separate claims.
A successful compile does not establish cable, firmware or macOS compatibility.

| Target | CI role | Hardware status |
| --- | --- | --- |
| Ubuntu 24.04 GA kernel | Build, QEMU, package lifecycle | Not independently validated |
| Debian 13 stock kernel | Build, QEMU, package lifecycle | Not independently validated |
| Ubuntu 26.04 GA kernel | Build, QEMU, package lifecycle | Linux 7.0 tested with a macOS peer |
| Proxmox `7.0.14-20-pve` | Manual builds with matching headers passed; not in CI | Temporary candidate A/B/A with a macOS peer; packaged DKMS lifecycle not validated |
| Linux 7.2 / current Arch kernel | Planned; excluded by the current DKMS build gate | Not validated |

The initial build gate is x86-64, Linux 6.8–6.19 or 7.0. This is an allowed
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

The manual Proxmox check compiled the unchanged 0.1.0 module and verified its
target kernel version metadata. It did not install or load the module. This is
build evidence only, not a successful Proxmox DKMS lifecycle or hardware test.

A subsequent candidate containing the 0.1.1 GRO header-length correction was
built and temporarily loaded on that Proxmox kernel for the A/B/A measurements
in [testing](testing.md). Its version label differed from the release package;
the driver source change was the same. That trial does not validate installation
or upgrade of the published `.deb`, reboot/hotplug behavior or long-term use.

The next milestone targets a current stable driver baseline with explicit
backward compatibility. Planned kernel families are not support guarantees;
see the [upstream tracking policy and roadmap](roadmap.md).
