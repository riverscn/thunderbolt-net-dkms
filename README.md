# thunderbolt-net-dkms

[简体中文](README.zh-CN.md)

An **experimental**, out-of-tree Linux `thunderbolt_net` driver packaged for DKMS.
It adds a receive workaround for oversized TCP packets from a peer:
validate the packet, attach conservative GSO metadata, and retain the aggregate
so local delivery stays fast and forwarding can segment when required.

## What problem does this address?

This project targets a specific Thunderbolt networking failure seen with a
**Mac Thunderbolt Bridge** connected to a Linux host: uploads from **macOS to
Linux** can become extremely slow when macOS TCP Segmentation Offload (TSO) is
enabled. The same connection may appear usable in the reverse direction, or
become less bad only after disabling TSO on the Mac.

The workaround is for the Linux receive and bridge/forwarding path. With
`rx_segment=1`, it validates the oversized TCP aggregate arriving from macOS
and supplies conservative GSO information before Linux forwards it. This lets
the Mac keep TSO enabled in the tested topology. It is relevant to searches for
terms such as **macOS Thunderbolt Bridge slow upload**, **Mac to Linux
Thunderbolt networking TSO**, **ThunderboltIP upload slow**, and **Proxmox
Thunderbolt Bridge**.

It does not claim that every slow Thunderbolt link has this cause. Cable or port
enumeration, host-router firmware, power management, MTU, DHCP, routing and
other offload interactions need separate diagnosis.

This is an independent project, not an upstream Linux, Apple, Intel, Ubuntu,
Debian, or Proxmox release. It is not a general fix for cable enumeration,
runtime power management, DHCP, or every TSO interoperability problem.

## Version 0.3.0

This iteration rebases the driver on Linux 7.2.9 and keeps the existing RX
changes: oversized-TCP normalization, optional page recycling, RX lifecycle
serialization and the 0.1.1 GRO header correction.

- The package now enables `rx_segment=1 rx_segment_mtu=1500 rx_page_pool=1`
  through one file, `/usr/lib/modprobe.d/thunderbolt-net.conf`. It is removed
  with the package; override it with the same name in `/etc/modprobe.d/`.
  The separate opt-in files of earlier versions are no longer needed.
- Upstream imports keep their original authorship; local integration and
  older-kernel compatibility are separate commits. See
  [upstream maintenance](docs/upstream-tracking.md).
- The build gate allows x86-64 Linux 6.8–6.19 and 7.0–7.2. CI also builds a
  checksum-pinned Linux 7.2.9 kernel. See [compatibility](docs/compatibility.md).
- On Linux 7.2 and newer cores the driver requests the previous 128-microsecond
  interrupt throttling itself; older cores keep their own moderation.

The [hardware regression](docs/upstream-validation.md) found throughput equal
to 0.2.0 on a Linux 7.0 host. On Linux 7.2.9, a build without the throttling
call had 11–44× more interrupts per GiB and about 20% lower download throughput.
No performance gain over 0.2.0 is claimed. Physical disconnect during traffic,
suspend/resume and long stress remain open.

Version 0.3.0 is the first release published without the prerelease mark. The
driver is still out of tree and its hardware validation covers one controller
and peer combination. Use an independent management path when reloading; [rollback](docs/installation.md#return-to-the-020-driver) is to 0.2.0.
The [0.2.0 report](docs/validation.md) covers the previous Linux v7.0 baseline.

## Install and enable

**Install prerequisites first.** On Debian / Ubuntu with their distribution
kernel:

```sh
sudo apt update
sudo apt install build-essential dkms curl ca-certificates coreutils kmod ethtool \
  "linux-headers-$(uname -r)"
```

On a Proxmox VE host with a Proxmox kernel, use this instead:

```sh
sudo apt update
sudo apt install build-essential dkms curl ca-certificates coreutils kmod ethtool \
  "proxmox-headers-$(uname -r)"
```

If already root, omit `sudo`. DKMS must be >= 3.0.10 and the headers must match
the **exact running kernel**. See [prerequisite checks](docs/installation.md#install-prerequisites-first).
Download the release package and verify `SHA256SUMS`, then install:

```sh
sudo apt install ./thunderbolt-net-dkms_0.3.0-1_all.deb
dkms status -m thunderbolt-net
modinfo -n thunderbolt_net
```

The `.deb` contains source, not a precompiled kernel or a machine-specific module.
DKMS builds the replacement for supported installed kernels. Package installation
does not request an active module reload. The currently loaded module stays in
memory until it is unloaded or the system reboots; `modinfo` describes the module
on disk, not necessarily the one currently running.

The package installs its defaults in `/usr/lib/modprobe.d/thunderbolt-net.conf`:

```conf
options thunderbolt_net rx_segment=1 rx_segment_mtu=1500 rx_page_pool=1
```

This enables the macOS TSO forwarding workaround and RX page recycling. The file
belongs to the package and is removed with it. On a kernel DKMS has not built
for, the distribution driver receives these options and ignores them with a
warning; check `dkms status`. Files that earlier versions asked
you to create, `/etc/modprobe.d/thunderbolt-net-rx.conf` and
`thunderbolt-net-page-pool.conf`, set the same values and can be deleted.

To change a value, copy the file to `/etc/modprobe.d/thunderbolt-net.conf` and
edit the copy; the same name in `/etc` replaces the package file, and upgrades
keep it. A differently named file does not reliably override it; see
[change or disable the defaults](docs/installation.md#change-or-disable-the-defaults).

Changes apply on the next module load. Refresh an affected initramfs first;
reload only from a console or independent management connection, since it
interrupts Thunderbolt networking. After reloading or rebooting, verify:

```sh
cat /sys/module/thunderbolt_net/parameters/rx_segment
cat /sys/module/thunderbolt_net/parameters/rx_segment_mtu
cat /sys/module/thunderbolt_net/parameters/rx_page_pool
```

With the defaults, expect `Y`, `1500`, and `Y`, respectively.

## Build

```sh
make check
make KERNELRELEASE="$(uname -r)"       # matching kernel headers required
dpkg-buildpackage --build=binary --no-sign
make dist
```

For Debian packaging, install `build-essential debhelper dh-dkms python3` first.
For kernel tests, see [testing](docs/testing.md). Test modules are excluded from
the DKMS package and run only in a diskless QEMU guest with no external NIC.

## CI and releases

GitHub Actions checks source hygiene, builds on Ubuntu 24.04, Debian 13,
Ubuntu 26.04, a pinned upstream Linux 7.2.9 kernel and the pinned Arch Linux
`7.2.9-arch1-1` kernel, runs kernel tests, and verifies DKMS install/removal in
isolated containers. Successful runs upload a `.deb`, an allowlisted source archive and
SHA-256 checksums. A matching `v*` tag publishes a **prerelease** only after all
jobs pass; promotion to a full release is a manual step after hardware
validation. See [release procedure](docs/releasing.md).

No private hardware runner or local network access is needed. Third-party
Actions are pinned to full commit hashes; pull requests receive read-only
permissions and no release token.

## Documentation

- [Installation, configuration and rollback](docs/installation.md)
- [Design and unsupported packet types](docs/design.md)
- [Kernel compatibility](docs/compatibility.md)
- [Upstream maintenance](docs/upstream-tracking.md) and [roadmap](docs/roadmap.md)
- [Tests](docs/testing.md) and [current hardware validation](docs/upstream-validation.md)
- [Privacy and safe bug reports](docs/privacy.md)
- [Source provenance](NOTICE.md), [contributing](CONTRIBUTING.md), [security](SECURITY.md)

## License and maintainer

GPL-2.0-only; see [LICENSE](LICENSE), retained source notices and
[Debian copyright metadata](debian/copyright).

Maintainer: Shun Li <riverscn@gmail.com>.
