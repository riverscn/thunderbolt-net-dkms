# thunderbolt-net-dkms

[简体中文](README.zh-CN.md)

An **experimental**, out-of-tree Linux `thunderbolt_net` driver packaged for DKMS.
It adds an opt-in receive workaround for oversized TCP packets from a peer:
validate the packet, attach conservative GSO metadata, and retain the aggregate
so local delivery stays fast and forwarding can segment when required.

This is an independent project, not an upstream Linux, Apple, Intel, Ubuntu,
Debian, or Proxmox release. It is not a general fix for cable enumeration,
runtime power management, DHCP, or every TSO interoperability problem.

## Version 0.2.0

This iteration adds opt-in RX page recycling, serializes RX startup/teardown,
and backports three upstream connection-cleanup fixes. It retains the 0.1.1
GRO ordering correction and conservative oversized-TCP normalization.

- `rx_page_pool=0` and `rx_segment=0` remain the defaults.
- Page recycling and TCP normalization can be enabled independently.
- The driver baseline remains Linux v7.0; Linux 7.2 and Arch packaging are
  outside this iteration. See [compatibility](docs/compatibility.md).
- See [RX lifecycle design](docs/rx-page-pool.md) and the current
  [validation report](docs/validation.md) for measured results and coverage.

Physical reconnection did not recover within the latest five-minute test
window; the PR remains draft. See the validation report for the observed failure.

The package remains experimental. Use an independent management path when
reloading; [rollback](docs/installation.md#return-to-the-011-baseline) is to
0.1.1. No stable release is declared by this PR.

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
sudo apt install ./thunderbolt-net-dkms_0.2.0-1_all.deb
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
SHA-256 checksums. A matching `v0.2.0` tag publishes an **experimental prerelease**
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
