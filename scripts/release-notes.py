#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Render installation-first GitHub release notes for the current version."""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def render(repository):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Expected a GitHub owner/repository')
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid release version')
    package = re.fullmatch(
        rf'thunderbolt-net \(({re.escape(version)}-\d+)\) .+',
        (ROOT / 'debian/changelog').read_text().splitlines()[0])
    if not package:
        raise ValueError('Debian changelog does not match VERSION')
    changes = re.search(
        rf'^## {re.escape(version)}(?:[ \t][^\n]*)?\n(.*?)(?=^## |\Z)',
        (ROOT / 'CHANGELOG.md').read_text(), re.MULTILINE | re.DOTALL)
    if not changes or not changes.group(1).strip():
        raise ValueError('Missing changelog entry for this release')
    url = f'https://github.com/{repository}'
    tag = f'v{version}'
    download = f'{url}/releases/download/{tag}'
    docs = f'{url}/blob/{tag}/docs'
    deb = f'thunderbolt-net-dkms_{package.group(1)}_all.deb'
    options = [line for line in (ROOT / 'packaging/thunderbolt-net.conf').read_text().splitlines()
               if line.strip() and not line.lstrip().startswith('#')]
    if len(options) != 1:
        raise ValueError('packaging/thunderbolt-net.conf must hold one options line')
    return f'''## Installation / upgrade

**{version}: Linux 7.2.9 driver baseline with older-kernel compatibility.**
The package now enables RX normalization and page recycling by default.
Review the [current validation report]({docs}/upstream-validation.md) before deployment.
This is an experimental, source-only DKMS package for x86-64 Linux. Use a
[supported kernel]({docs}/compatibility.md), its exact matching development
headers, and DKMS >= 3.0.10.

**1. Install prerequisites before the driver package.** Choose the command for
your kernel provider. If already root (for example, in the Proxmox host console),
omit `sudo`.

Debian / Ubuntu with their distribution kernel:

```sh
sudo apt update
sudo apt install build-essential dkms curl ca-certificates coreutils kmod ethtool \\
  "linux-headers-$(uname -r)"
```

Proxmox VE host with a Proxmox kernel:

```sh
sudo apt update
sudo apt install build-essential dkms curl ca-certificates coreutils kmod ethtool \\
  "proxmox-headers-$(uname -r)"
```

These install the compiler/build tools, DKMS, exact kernel headers, and the
download/checksum/module/network tools used in this guide. Proxmox requires
**Proxmox headers** matching the complete `uname -r` value, including `-pve`;
generic Debian/Ubuntu headers cannot replace them. If the exact headers are
unavailable, check the vendor repositories or boot a supported kernel with
matching headers before continuing.

```sh
dkms --version
test -r "/lib/modules/$(uname -r)/build/Makefile" && echo "Kernel headers found"
```

Confirm DKMS >= 3.0.10 and a successful header check.

**2. Download, verify, and install this version.** Run in a new directory:

```sh
curl -fLO {download}/{deb}
curl -fLO {download}/SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS && \\
  sudo apt install ./{deb}
dkms status -m thunderbolt-net
modinfo -n thunderbolt_net
modinfo -F version thunderbolt_net
```

`--ignore-missing` skips the source archive if you only downloaded the `.deb`.
Confirm that the `.deb` checksum is `OK`. Installing the package does not replace
the driver already loaded in memory.

**3. Review the default configuration.** The package installs
`/usr/lib/modprobe.d/thunderbolt-net.conf`, removed again with the package:

```conf
{options[0]}
```

It enables the macOS TSO forwarding workaround and RX page recycling. Opt-in
files from earlier versions set the same values and are no longer needed:

```sh
sudo rm -f /etc/modprobe.d/thunderbolt-net-rx.conf \\
  /etc/modprobe.d/thunderbolt-net-page-pool.conf
```

**4. Change a default only if needed.** Copy the file to `/etc/modprobe.d/`
under the same name and edit the copy, for example `rx_page_pool=0`:

```sh
sudo cp /usr/lib/modprobe.d/thunderbolt-net.conf /etc/modprobe.d/thunderbolt-net.conf
sudoedit /etc/modprobe.d/thunderbolt-net.conf
```

The same name in `/etc` replaces the package file and survives upgrades; a file
with another name may sort before it and have no effect. Shared NAPI/lifecycle
changes are active even when page recycling is disabled; see the
[RX lifecycle design]({docs}/rx-page-pool.md).

**5. Activate and verify.** Reload from a local console or an independent
management connection; this interrupts Thunderbolt networking:

```sh
sudo modprobe -r thunderbolt_net && sudo modprobe thunderbolt_net
cat /sys/module/thunderbolt_net/version
cat /sys/module/thunderbolt_net/parameters/rx_segment
cat /sys/module/thunderbolt_net/parameters/rx_page_pool
```

Expect version `{version}` and `Y` for both parameters with the defaults. A reboot can be
used instead of a reload. If the module is in an initramfs, refresh that image
first. Secure Boot may require enrolling the local DKMS signing key.
See the **[full installation and rollback guide]({docs}/installation.md)**.

**6. Rollback choices.** To disable only page recycling, set `rx_page_pool=0` in
`/etc/modprobe.d/thunderbolt-net.conf` (step 4), refresh an affected initramfs
and reload. To return to the **previous 0.2.0 driver** (Linux v7.0 baseline, same
options), download and verify the [v0.2.0 package]({url}/releases/tag/v0.2.0)
in a new directory. 0.2.0 installs no defaults and starts with every option off,
so keep the current settings in `/etc` first:

```sh
sudo cp /usr/lib/modprobe.d/thunderbolt-net.conf /etc/modprobe.d/thunderbolt-net.conf
sudo apt install --allow-downgrades ./thunderbolt-net-dkms_0.2.0-1_all.deb
```

Confirm the downgrade succeeds. Refresh any affected initramfs, reload from an
independent console or reboot, and verify the running version is `0.2.0`. After
a later upgrade, delete that `/etc` copy unless you changed it, or it keeps
replacing the package defaults. For the older 0.1.1 baseline, see the
[complete rollback steps]({docs}/installation.md#return-to-the-011-baseline).

## Changes in {tag}

{changes.group(1).strip()}

## Validation and assets

CI checks source/privacy rules, module builds, isolated QEMU packet tests, and
DKMS installation/removal on three distributions and a checksum-pinned upstream
Linux 7.2.9 kernel, including which ring-throttling path each module uses.
Ubuntu 26.04 additionally runs 63 RX model cases and a missing-sync negative
control. KASAN/KCSAN experiments are documented manual runs, not hosted CI or
actual Thunderbolt validation. See
[testing and hardware limitations]({docs}/testing.md).

Assets: source-only DKMS `.deb`, source archive, and `SHA256SUMS`.
No full kernel or precompiled module is included.
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default='riverscn/thunderbolt-net-dkms')
    args = parser.parse_args()
    try:
        print(render(args.repository), end='')
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
