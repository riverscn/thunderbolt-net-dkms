#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Disposable Arch Linux CI container only; no host module trees or hardware.
# Tests the distribution's own kernel build and headers layout, pinned to the
# Arch Linux Archive packages in tests/kernels/arch.json.
set -euo pipefail
cd "$(dirname "$0")/.."
# pacman 7's download sandbox needs Landlock, which CI container hosts may lack.
grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
pacman -Sy --noconfirm --needed archlinux-keyring
pacman -Su --noconfirm --needed base-devel git python dkms kmod busybox cpio \
    zstd xz iproute2 qemu-system-x86 curl ca-certificates
mkdir -p build/arch-kernel
python3 - build/arch-kernel <<'PY'
import hashlib, json, pathlib, sys, urllib.request
lock = json.loads(pathlib.Path('tests/kernels/arch.json').read_text())
out = pathlib.Path(sys.argv[1])
for pkg in lock['packages']:
    path = out / pkg['url'].rsplit('/', 1)[1]
    with urllib.request.urlopen(pkg['url'], timeout=600) as r, path.open('wb') as f:
        while data := r.read(1024 * 1024):
            f.write(data)
    if hashlib.sha256(path.read_bytes()).hexdigest() != pkg['sha256']:
        sys.exit('checksum mismatch: ' + path.name)
PY
# The kernel's initramfs hooks are irrelevant in a diskless guest.
pacman -U --noconfirm --assume-installed initramfs build/arch-kernel/*.pkg.tar.zst
kernel=$(python3 -c 'import json; print(json.load(open("tests/kernels/arch.json"))["kernel"])')
test -d "/usr/lib/modules/$kernel/build"
install -D -m 644 "/usr/lib/modules/$kernel/vmlinuz" "/boot/vmlinuz-$kernel"

make check
python3 scripts/qemu-test.py --kernel "$kernel"

# Actual DKMS build/install/remove with Arch's dkms against the same kernel.
version=$(cat VERSION)
source_dir="/usr/src/thunderbolt-net-$version"
mkdir -p "$source_dir/src"
cp VERSION Makefile dkms.conf "$source_dir/"
cp src/Makefile src/*.c src/*.h "$source_dir/src/"
original=$(modinfo -k "$kernel" -F filename thunderbolt_net)
original_hash=$(sha256sum "$original" | cut -d ' ' -f 1)
dkms add -m thunderbolt-net -v "$version"
dkms install -m thunderbolt-net -v "$version" -k "$kernel"
selected=$(modinfo -k "$kernel" -F filename thunderbolt_net)
case "$selected" in */updates/dkms/*) ;; *) echo "DKMS module was not selected: $selected" >&2; exit 1 ;; esac
test "$(modinfo -k "$kernel" -F version thunderbolt_net)" = "$version"
bash scripts/check-throttling.sh "$kernel" "$selected" --require
dkms remove -m thunderbolt-net -v "$version" --all
test "$(modinfo -k "$kernel" -F filename thunderbolt_net)" = "$original"
test "$(sha256sum "$original" | cut -d ' ' -f 1)" = "$original_hash"
echo "Validated Arch kernel and DKMS restoration: $kernel"
