# Installation and rollback

## Requirements

Use an x86-64 system with a supported kernel, matching development headers,
DKMS >= 3.0.10, and an enabled Thunderbolt networking subsystem.
The stock network driver must be a loadable module (`CONFIG_THUNDERBOLT_NET=m`
or `CONFIG_USB4_NET=m`, depending on kernel). A driver built into the kernel
cannot be replaced this way.

## Install prerequisites first

Before installing the `.deb`, install DKMS, the compiler/build tools, the headers
for the **currently running kernel**, and the download/verification tools used
below. Choose the command for your kernel provider. Commands use `sudo`; when
already root (for example, in the Proxmox host console), omit `sudo`.

**Debian / Ubuntu with their distribution kernel:**

```sh
sudo apt update
sudo apt install build-essential dkms curl ca-certificates coreutils kmod ethtool \
  "linux-headers-$(uname -r)"
```

**Proxmox VE host with a Proxmox kernel:**

```sh
sudo apt update
sudo apt install build-essential dkms curl ca-certificates coreutils kmod ethtool \
  "proxmox-headers-$(uname -r)"
```

`build-essential` supplies the compiler and `make`; `dkms` builds and manages the
module. `curl` and `ca-certificates` support HTTPS downloads; `coreutils` supplies
`sha256sum`; `kmod` and `ethtool` provide module and network verification tools.
APT also installs these packages' dependencies.

Proxmox headers must match the complete `uname -r` value, including `-pve`.
Generic Debian/Ubuntu headers cannot replace them. If the exact header package
is unavailable, check the distribution/vendor repositories or boot a supported
kernel with available matching headers before continuing.

Check the prerequisites:

```sh
dkms --version
test -r "/lib/modules/$(uname -r)/build/Makefile" && echo "Kernel headers found"
```

DKMS must be at least 3.0.10, and the header check must succeed. Requirements
above apply to both release-package and direct DKMS source installation.
See the [Debian kernel header documentation](https://kernel-team.pages.debian.net/kernel-handbook/ch-packaging.html)
and [Proxmox header package definitions](https://github.com/proxmox/pve-kernel/blob/master/debian/control.in).

Only when **building the Debian package itself from source**, also install:

```sh
sudo apt install debhelper dh-dkms python3
```

`make check` requires Python 3.9 or newer. No test requires a Thunderbolt cable
unless explicitly marked as a hardware test.

## Install a release package

Check `SHA256SUMS` against the release files, then:

```sh
sudo apt install ./thunderbolt-net-dkms_0.3.0-1_all.deb
dkms status -m thunderbolt-net
modinfo -n thunderbolt_net
modinfo -F version thunderbolt_net
```

The kernel module retains the stock name `thunderbolt_net`. DKMS manages an
override (normally in `updates/dkms`) and original-module restoration. Do not
copy the module over the distribution's file or blacklist `thunderbolt_net`:
blacklisting that name affects the replacement too. Concurrent third-party
DKMS replacements for the same module are not supported.

The package ships no enabled modprobe configuration, module-load service,
network configuration, firewall rule, or power-management policy. The package
does not itself request module reloads. System-wide DKMS hooks and policies are
administrator-controlled and may perform additional actions.

## Explicitly enable RX normalization

The GRO header-length correction in 0.1.1 is active whenever this module is
loaded. The following opt-in only controls oversized TCP RX normalization.

Copy the example from the source tree or installed documentation:

```sh
sudo install -m 644 \
  /usr/share/doc/thunderbolt-net-dkms/examples/thunderbolt-net-rx.conf.example \
  /etc/modprobe.d/thunderbolt-net-rx.conf
```

From a local console or a separate management connection:

```sh
sudo modprobe -r thunderbolt_net
sudo modprobe thunderbolt_net
cat /sys/module/thunderbolt_net/version
cat /sys/module/thunderbolt_net/parameters/rx_segment
ethtool -S thunderbolt0
```

Expected: module version `0.3.0`, `rx_segment` is `Y`, and normalization counters
increase under suitable traffic. Interface names can differ. An unload failure
must be investigated; never force-remove a busy module. Reloading interrupts
Thunderbolt networking, and peer negotiation may take time. Keep the console
available until addressing and connectivity have returned.

If the module is included in an initramfs, update that image after installing
or removing the override/configuration. On Debian-family systems the usual
command is `sudo update-initramfs -u -k "$(uname -r)"`. Check its output. A reboot
activates the on-disk module but is not performed by this package.

## Enable or disable RX page recycling

Version 0.2.0 added a separate, default-off `rx_page_pool` switch. See the
[RX lifecycle design](rx-page-pool.md). To opt in:

```sh
sudo install -m 644 \
  /usr/share/doc/thunderbolt-net-dkms/examples/thunderbolt-net-page-pool.conf.example \
  /etc/modprobe.d/thunderbolt-net-page-pool.conf
```

This file can coexist with `thunderbolt-net-rx.conf`: both contain `options`
for `thunderbolt_net`, and [modprobe.d(5)](https://manpages.ubuntu.com/manpages/noble/man5/modprobe.d.5.html)
combines their distinct parameters. Together they are equivalent to:

```conf
options thunderbolt_net rx_segment=1 rx_segment_mtu=1500 rx_page_pool=1
```

Separate files are a convenience, not a requirement. Use one layout and avoid
repeating the same parameter with conflicting values. Removing the page-pool
file leaves RX normalization configured; either change needs a module reload
to affect the running driver.

Reload from a console/independent connection as above and verify
`cat /sys/module/thunderbolt_net/parameters/rx_page_pool` returns `Y`.
To disable just the pool, remove that opt-in file (or set `rx_page_pool=0`), refresh
an affected initramfs, then reload. Keep the RX normalization file if needed.
The NAPI allocator/lifecycle changes remain active with the pool disabled.

## Return to the 0.2.0 driver

Version 0.2.0 uses the Linux v7.0 driver baseline and supports the same
`rx_segment`, `rx_segment_mtu` and `rx_page_pool` options, so existing opt-in
files remain valid. From a **new download directory**, verify and downgrade:

```sh
curl -fLO https://github.com/riverscn/thunderbolt-net-dkms/releases/download/v0.2.0/thunderbolt-net-dkms_0.2.0-1_all.deb
curl -fLO https://github.com/riverscn/thunderbolt-net-dkms/releases/download/v0.2.0/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS && \
  sudo apt install --allow-downgrades ./thunderbolt-net-dkms_0.2.0-1_all.deb
```

Confirm the package checksum is `OK` and the downgrade succeeds. Check
`dkms status`, refresh any affected initramfs, then reload from an independent
console or reboot. `cat /sys/module/thunderbolt_net/version` must report `0.2.0`
afterward. Installing an older package does not replace the already loaded module.

## Return to the 0.1.1 baseline

For the older baseline without page recycling, use the 0.1.1 release package.
From a **new download directory**, verify and downgrade:

```sh
curl -fLO https://github.com/riverscn/thunderbolt-net-dkms/releases/download/v0.1.1/thunderbolt-net-dkms_0.1.1-1_all.deb
curl -fLO https://github.com/riverscn/thunderbolt-net-dkms/releases/download/v0.1.1/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS && \
  sudo apt install --allow-downgrades ./thunderbolt-net-dkms_0.1.1-1_all.deb
sudo rm -f /etc/modprobe.d/thunderbolt-net-page-pool.conf
```

Confirm the package checksum is `OK` and the downgrade succeeds. Remove any
`rx_page_pool` option you added elsewhere too; 0.1.1 does not support it. Retain
`thunderbolt-net-rx.conf` if the TSO workaround is needed. Check `dkms status`,
refresh any affected initramfs, then reload from an independent console or
reboot. `cat /sys/module/thunderbolt_net/version` must report `0.1.1` afterward.
Installing an older package does not replace the already loaded module.

## Secure Boot

DKMS supports locally signed modules. With Secure Boot enabled, the signing key
must be trusted by the firmware/kernel (often via MOK enrollment). Use your
distribution's supported enrollment flow and verify the signer with `modinfo`.
Private signing keys belong on the installation machine and must never be
committed or included in release artifacts. This project does not distribute a
universal signing key or require disabling Secure Boot.

See the [DKMS signing documentation](https://github.com/dkms-project/dkms#module-signing).

## Disable the workaround or remove the package

To retain this module but disable normalization, remove the opt-in configuration
or set `rx_segment=0`, then reload from an independent connection. The GRO
header-length correction remains active; removing the package restores the
distribution driver's behavior after the next module load.

To restore the distribution module:

```sh
sudo apt purge thunderbolt-net-dkms
sudo rm -f /etc/modprobe.d/thunderbolt-net-rx.conf \
  /etc/modprobe.d/thunderbolt-net-page-pool.conf
sudo depmod -a
modinfo -n thunderbolt_net
```

Remove only the opt-in files you created for this project. Confirm that `modinfo`
resolves to the distribution module. Refresh any affected initramfs, then reload
`thunderbolt_net` from the console or reboot when convenient. Package removal
does not replace the module already resident in memory. If DKMS reports a
collision or failed restoration, stop and inspect its state before reloading.

## Direct DKMS installation from source

```sh
sudo dkms add .
sudo dkms install -m thunderbolt-net -v 0.3.0 -k "$(uname -r)"
```

For this installation method, remove with
`sudo dkms remove -m thunderbolt-net -v 0.3.0 --all`, then follow the same
configuration/initramfs/reload steps. Do not mix manual and Debian-managed
installations of the same version.
