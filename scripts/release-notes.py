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
    return f'''## Installation / upgrade

**{version}: optional RX page recycling and RX lifecycle fixes.**
Page recycling is disabled by default. Review the
[current validation report]({docs}/validation.md) before deployment.
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

**3. Opt in to oversized TCP RX normalization if needed.** To enable the
macOS TSO forwarding workaround, install the provided configuration:

```sh
sudo install -m 644 \\
  /usr/share/doc/thunderbolt-net-dkms/examples/thunderbolt-net-rx.conf.example \\
  /etc/modprobe.d/thunderbolt-net-rx.conf
```

An existing opt-in configuration is retained on upgrade.

**4. Opt in to the RX page recycling separately.** It is disabled by default;
install this file to test `rx_page_pool=1`:

```sh
sudo install -m 644 \\
  /usr/share/doc/thunderbolt-net-dkms/examples/thunderbolt-net-page-pool.conf.example \\
  /etc/modprobe.d/thunderbolt-net-page-pool.conf
```

This does not replace the `rx_segment` setting. Shared NAPI/lifecycle changes
are active even when page recycling is disabled; see the
[RX lifecycle design]({docs}/rx-page-pool.md).

**5. Activate and verify.** Reload from a local console or an independent
management connection; this interrupts Thunderbolt networking:

```sh
sudo modprobe -r thunderbolt_net && sudo modprobe thunderbolt_net
cat /sys/module/thunderbolt_net/version
cat /sys/module/thunderbolt_net/parameters/rx_segment
cat /sys/module/thunderbolt_net/parameters/rx_page_pool
```

Expect version `{version}` and `Y` for each parameter you opted in to. A reboot can be
used instead of a reload. If the module is in an initramfs, refresh that image
first. Secure Boot may require enrolling the local DKMS signing key.
See the **[full installation and rollback guide]({docs}/installation.md)**.

**6. Rollback choices.** To disable only page recycling, remove
`/etc/modprobe.d/thunderbolt-net-page-pool.conf`, refresh an affected initramfs and
reload. For the **complete 0.1.1 baseline**, download and verify the
[v0.1.1 package]({url}/releases/tag/v0.1.1) in a new directory, then:

```sh
sudo apt install --allow-downgrades ./thunderbolt-net-dkms_0.1.1-1_all.deb
sudo rm -f /etc/modprobe.d/thunderbolt-net-page-pool.conf
```

Confirm the downgrade succeeds; remove any `rx_page_pool` option added elsewhere
before loading 0.1.1. Keep `rx_segment` configured if needed. Refresh any affected
initramfs, reload from an independent console or reboot, and verify the running
version is `0.1.1`. See the [complete rollback steps]({docs}/installation.md#return-to-the-011-baseline).

## Changes in {tag}

{changes.group(1).strip()}

## Validation and assets

CI checks source/privacy rules, module builds, isolated QEMU packet tests, and
DKMS installation/removal on three distributions. Ubuntu 26.04 additionally
runs 63 RX model cases and a missing-sync negative control. KASAN/KCSAN experiments
are documented manual runs, not hosted CI or actual Thunderbolt validation. See
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
