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

The package installs one modprobe configuration file with the driver defaults
(next section). It ships no module-load service, network configuration, firewall
rule, or power-management policy, and does not itself request module reloads. System-wide DKMS hooks and policies are
administrator-controlled and may perform additional actions.

## Default configuration

The package installs `/usr/lib/modprobe.d/thunderbolt-net.conf`:

```conf
options thunderbolt_net rx_segment=1 rx_segment_mtu=1500 rx_page_pool=1
```

This enables oversized TCP RX normalization (the macOS TSO forwarding
workaround) and RX page recycling ([design](rx-page-pool.md)) for the next
module load. The file belongs to the package: upgrades replace it and removing
the package deletes it, so no options remain once the package is gone.

While the package is installed, the options apply to whichever `thunderbolt_net`
loads. If DKMS has not built this module for the running kernel (a kernel
outside the [supported range](compatibility.md), missing headers or a failed
build), the distribution driver loads instead, logs
`thunderbolt_net: unknown parameter 'rx_segment' ignored` (and likewise for the
others) and runs without the workaround. Check with
`dkms status -m thunderbolt-net` and `cat /sys/module/thunderbolt_net/version`.
The module's built-in defaults are unchanged (all off), which matters only for
direct source builds loaded without this file. The GRO header-length correction
from 0.1.1 is active whenever this module is loaded, independent of options.

Earlier versions asked you to create `/etc/modprobe.d/thunderbolt-net-rx.conf`
and `/etc/modprobe.d/thunderbolt-net-page-pool.conf`. They set the same values
and can stay, but are no longer needed:

```sh
sudo rm -f /etc/modprobe.d/thunderbolt-net-rx.conf \
  /etc/modprobe.d/thunderbolt-net-page-pool.conf
```

To activate the configuration, reload from a local console or a separate
management connection, or reboot:

```sh
sudo systemctl stop ifup@thunderbolt0.service   # ifupdown hosts only, see below
sudo rmmod thunderbolt_net
sudo modprobe thunderbolt_net
sudo udevadm settle
sudo systemctl start ifup@thunderbolt0.service  # ifupdown hosts only
cat /sys/module/thunderbolt_net/version
cat /sys/module/thunderbolt_net/parameters/rx_segment
cat /sys/module/thunderbolt_net/parameters/rx_segment_mtu
cat /sys/module/thunderbolt_net/parameters/rx_page_pool
ethtool -S thunderbolt0
```

Expected: module version `0.3.0`, then `Y`, `1500` and `Y`, and normalization
counters increase under suitable traffic. Interface names can differ.

Use `rmmod`, not `modprobe -r`. Removing the network driver with modprobe also
unloads the Thunderbolt core module `thunderbolt` once nothing else uses it.
That drops the USB4 link, and the peer may not reconnect until the cable is
replugged. `rmmod` unloads only the network driver, so the link stays up and
carrier normally returns within a few seconds after `modprobe`.

The two `systemctl` lines apply to Debian and Proxmox hosts whose
`/etc/network/interfaces` brings the port up through ifupdown hotplug
(`allow-hotplug thunderbolt0`). Removing the driver starts stopping
`ifup@thunderbolt0.service`; if the new interface appears before that stop
finishes, its hotplug `ifup` does not run, and the interface stays `DOWN` and
outside its bridge. Stopping the unit first and starting it after the new
interface exists avoids the race; if it already happened, the `start` line
alone recovers. Use your interface name, and omit both lines with systemd-networkd,
NetworkManager or no automatic configuration.

An unload failure must be investigated; never force-remove a busy module.
Reloading interrupts Thunderbolt networking, and peer negotiation may take
time. Keep the console available until addressing and connectivity have
returned.

If the module is included in an initramfs, update that image after installing,
upgrading or removing the package or changing the configuration. On
Debian-family systems the usual command is
`sudo update-initramfs -u -k "$(uname -r)"`. Check its output. A reboot
activates the on-disk module but is not performed by this package.

## Change or disable the defaults

Copy the package file to `/etc/modprobe.d/` under the **same name** and edit the
copy. It then replaces the package file permanently, including any defaults
that later versions change; review it after upgrades. A file of the same name in `/etc/modprobe.d/` replaces the one in
`/usr/lib/modprobe.d/` (see [modprobe.d(5)](https://manpages.ubuntu.com/manpages/noble/man5/modprobe.d.5.html)),
and package upgrades leave it alone:

```sh
sudo cp /usr/lib/modprobe.d/thunderbolt-net.conf /etc/modprobe.d/thunderbolt-net.conf
sudoedit /etc/modprobe.d/thunderbolt-net.conf
```

For example, `options thunderbolt_net rx_segment=1 rx_segment_mtu=1500 rx_page_pool=0`
keeps the workaround and disables page recycling; `rx_segment=0` disables
normalization. The NAPI allocator/lifecycle changes remain active with the pool
disabled. Do not use a differently named file to override a value: modprobe
reads files in name order and the kernel keeps the last value, so a name that
sorts before `thunderbolt-net.conf` (such as `thunderbolt-net-local.conf`) has
no effect. Refresh an affected initramfs, then reload as above and check the
`parameters/` values.

## Return to the 0.2.0 driver

Version 0.2.0 uses the Linux v7.0 driver baseline and supports the same
`rx_segment`, `rx_segment_mtu` and `rx_page_pool` options, but it does not
install default configuration: the downgrade removes
`/usr/lib/modprobe.d/thunderbolt-net.conf`, and 0.2.0 starts with every option
off. An `/etc/modprobe.d/thunderbolt-net.conf` you created stays in effect. To
keep the current behavior without one, create it before downgrading:

```sh
sudo cp /usr/lib/modprobe.d/thunderbolt-net.conf /etc/modprobe.d/thunderbolt-net.conf
```

That copy keeps replacing the package file after any later upgrade. When you
upgrade again, delete it unless you changed it, so new package defaults apply:
`sudo rm /etc/modprobe.d/thunderbolt-net.conf`.

From a **new download directory**, verify and downgrade:

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

Confirm the package checksum is `OK` and the downgrade succeeds. The downgrade
removes the package defaults, and 0.1.1 does not support `rx_page_pool`: remove
it from any file you created, such as `/etc/modprobe.d/thunderbolt-net.conf`.
To keep the TSO workaround, leave only
`options thunderbolt_net rx_segment=1 rx_segment_mtu=1500` in that file. Check `dkms status`,
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

To retain this module but disable normalization, set `rx_segment=0` in
`/etc/modprobe.d/thunderbolt-net.conf` ([change the defaults](#change-or-disable-the-defaults)),
then reload from an independent connection. The GRO header-length correction
remains active; removing the package restores the distribution driver's
behavior after the next module load.

To restore the distribution module:

```sh
sudo apt purge thunderbolt-net-dkms
sudo rm -f /etc/modprobe.d/thunderbolt-net.conf \
  /etc/modprobe.d/thunderbolt-net-rx.conf \
  /etc/modprobe.d/thunderbolt-net-page-pool.conf
sudo depmod -a
modinfo -n thunderbolt_net
```

Removing the package deletes `/usr/lib/modprobe.d/thunderbolt-net.conf`. Remove
only the files in `/etc/modprobe.d/` that you created for this project; the
distribution driver does not know these options. Confirm that `modinfo`
resolves to the distribution module. Refresh any affected initramfs, then reload
`thunderbolt_net` from the console or reboot when convenient. Package removal
does not replace the module already resident in memory. If DKMS reports a
collision or failed restoration, stop and inspect its state before reloading.

## Direct DKMS installation from source

```sh
sudo dkms add .
sudo dkms install -m thunderbolt-net -v 0.3.0 -k "$(uname -r)"
```

This does not install the package defaults; the module then loads with every
option off. To use them, install the same file under `/etc`:

```sh
sudo install -m 644 packaging/thunderbolt-net.conf /etc/modprobe.d/thunderbolt-net.conf
```

For this installation method, remove with
`sudo dkms remove -m thunderbolt-net -v 0.3.0 --all` and delete
`/etc/modprobe.d/thunderbolt-net.conf`, then follow the same initramfs/reload
steps. Do not mix manual and Debian-managed
installations of the same version.
