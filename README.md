# thunderbolt-net-dkms

[简体中文](README.zh-CN.md)

An out-of-tree Linux `thunderbolt_net` driver packaged for DKMS. It tracks the
upstream driver (Linux 7.2.9, imports keep their authorship) and carries a
**receive-side workaround** for peers that send TCP aggregates without segment
boundaries, as the macOS Thunderbolt Bridge does with TSO enabled: the driver
validates the aggregate, attaches conservative GSO metadata bounded by the
flow's learned path MTU, and keeps the aggregate intact, so local delivery
stays fast and forwarding segments only where the path requires it.

It is a workaround by nature: the ThunderboltIP transport carries no segment
size, so the driver reconstructs what the peer did not send, within limits it
learns from the SYN-ACK and ICMP that pass through the host. The driver base,
the GRO header correction, the RX lifecycle fixes and page recycling are
ordinary driver work; the learned bounds are new in 0.4.0 and experimental.

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

## Current release: 0.4.0

- Driver base: upstream Linux 7.2.9 with older-kernel compatibility; the
  DKMS build gate allows x86-64 Linux 6.8–6.19 and 7.0–7.2
  ([compatibility](docs/compatibility.md), [upstream maintenance](docs/upstream-tracking.md)).
- The package enables RX normalization and page recycling through
  `/usr/lib/modprobe.d/thunderbolt-net.conf`; override it with the same file
  name in `/etc/modprobe.d/` ([installation](docs/installation.md)).
- Each forwarded flow's path MTU is learned from the SYN-ACK and ICMP that pass
  through the host (`rx_segment_learn`, experimental, 0.4.0); `rx_segment_mtu`
  is the fallback for flows without a record, 1280 since 0.3.1
  ([path MTU](docs/path-mtu.md)). If you copied the file to `/etc` before
  0.3.1, your copy still says 1500.
- Hardware validation covers one controller and one macOS peer; see the
  [0.3.0 regression](docs/upstream-validation.md) and the
  [path MTU measurements](docs/path-mtu.md). Physical disconnect during
  traffic, suspend/resume and long stress remain open.

Earlier versions and their changes are in [CHANGELOG.md](CHANGELOG.md);
rollback steps are in [installation](docs/installation.md#return-to-the-020-driver).

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
In a **new, empty directory**, download the newest release's package and
checksum file, verify, and install:

```sh
curl -fsSL https://api.github.com/repos/riverscn/thunderbolt-net-dkms/releases/latest \
  | grep -oE '"browser_download_url": *"[^"]*(_all\.deb|/SHA256SUMS)"' | cut -d'"' -f4 \
  | xargs -n1 curl -fLO
sha256sum --check --ignore-missing SHA256SUMS && sudo apt install ./thunderbolt-net-dkms_*_all.deb
dkms status -m thunderbolt-net
modinfo -n thunderbolt_net
```

The first command asks the GitHub API for the latest full release (prereleases
are skipped) and downloads its `.deb` and `SHA256SUMS`. Confirm that the
checksum line reports `OK` before installing. To install a prerelease or pin a
version, download both files from that release's page on
[Releases](https://github.com/riverscn/thunderbolt-net-dkms/releases) and run
the same two commands.

The `.deb` contains source, not a precompiled kernel or a machine-specific module.
DKMS builds the replacement for supported installed kernels. Package installation
does not request an active module reload. The currently loaded module stays in
memory until it is unloaded or the system reboots; `modinfo` describes the module
on disk, not necessarily the one currently running.

The package installs its defaults in `/usr/lib/modprobe.d/thunderbolt-net.conf`:

```conf
options thunderbolt_net rx_segment=1 rx_segment_mtu=1280 rx_page_pool=1
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

With the defaults, expect `Y`, `1280`, and `Y`, respectively.

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
