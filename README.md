# thunderbolt-net-dkms

[简体中文](README.zh-CN.md)

An **experimental**, out-of-tree Linux `thunderbolt_net` driver packaged for DKMS.
It adds an opt-in receive workaround for oversized TCP packets from a peer:
validate the packet, attach conservative GSO metadata, and retain the aggregate
so local delivery stays fast and forwarding can segment when required.

This is an independent project, not an upstream Linux, Apple, Intel, Ubuntu,
Debian, or Proxmox release. It is not a general fix for cable enumeration,
runtime power management, DHCP, or every TSO interoperability problem.

## Status

- Derived from the Linux v7.0 driver; original notices are retained.
- Version 0.1.1 corrects Ethernet-header accounting for GRO flow matching and
  packet ordering. This correction is active with either `rx_segment` setting.
- Hardware validation: Linux 7.0, macOS peer with TSO enabled, MTU 1500.
- Native IPv4 throughput was within about 2% of the stock driver in three
  alternating short trials. See [measurements and limits](docs/testing.md).
- Real IPv4 Docker forwarding and synthetic IPv4/IPv6 bridge/router paths were
  tested. Physical two-port bridge forwarding and long-duration stability have
  not been fully validated.
- Oversized TCP RX normalization remains disabled by default: `rx_segment=0`.
- Initial packaging targets x86-64 Linux; see [compatibility](docs/compatibility.md).

This remains an experimental development release. The next milestone will move
the driver to a pinned current stable upstream release and add a focused compatibility layer
for selected older kernels. That work, Linux 7.2 validation, and Arch packaging
are **planned, not implemented in 0.1.1**. See the [roadmap](docs/roadmap.md).

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
sudo apt install ./thunderbolt-net-dkms_0.1.1-1_all.deb
dkms status -m thunderbolt-net
modinfo -n thunderbolt_net
```

The `.deb` contains source, not a precompiled kernel or a machine-specific module.
DKMS builds the replacement for supported installed kernels. Package installation
does not request an active module reload. The currently loaded module stays in
memory until it is unloaded or the system reboots; `modinfo` describes the module
on disk, not necessarily the one currently running.

To enable the workaround on the next module load, after reviewing the
[installation and rollback guide](docs/installation.md):

```sh
sudo install -m 644 packaging/thunderbolt-net-rx.conf.example \
  /etc/modprobe.d/thunderbolt-net-rx.conf
```

Reload only from a console or independent management connection; it interrupts
Thunderbolt networking. The guide covers Secure Boot, initramfs and recovery.
The example is also installed under `/usr/share/doc/thunderbolt-net-dkms/examples/`.

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

GitHub Actions checks source hygiene, builds on Ubuntu 24.04, Debian 13 and
Ubuntu 26.04, runs kernel tests, and verifies package install/removal in isolated
containers. Successful runs upload a `.deb`, an allowlisted source archive and
SHA-256 checksums. A matching `v0.1.1` tag publishes an **experimental prerelease**
only after all jobs pass. See [release procedure](docs/releasing.md).

No private hardware runner or local network access is needed. Third-party
Actions are pinned to full commit hashes; pull requests receive read-only
permissions and no release token.

## Documentation

- [Installation, opt-in and rollback](docs/installation.md)
- [Design and unsupported packet types](docs/design.md)
- [Kernel compatibility](docs/compatibility.md)
- [Upstream tracking and development roadmap](docs/roadmap.md)
- [Tests and anonymized performance evidence](docs/testing.md)
- [Privacy and safe bug reports](docs/privacy.md)
- [Source provenance](NOTICE.md), [contributing](CONTRIBUTING.md), [security](SECURITY.md)

## License and maintainer

GPL-2.0-only; see [LICENSE](LICENSE), retained source notices and
[Debian copyright metadata](debian/copyright).

Maintainer: Shun Li <riverscn@gmail.com>.
