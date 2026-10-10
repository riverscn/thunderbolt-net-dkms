#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Disposable CI container only; no host module trees or hardware passthrough.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
export DEBIAN_FRONTEND=noninteractive
install_dependencies() {
    apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=3 update -qq
    apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=3 install -y --no-install-recommends \
        build-essential bc bison flex libssl-dev libelf-dev python3 kmod git \
        busybox-static iproute2 qemu-system-x86 cpio zstd xz-utils curl ca-certificates dkms
}
# CI installs dependencies first so the kernel-tree cache step can use zstd.
if test "${1:-}" = deps; then
    install_dependencies
    exit 0
fi
command -v dkms >/dev/null && command -v qemu-system-x86_64 >/dev/null || install_dependencies
mkdir -p build/upstream-kernel
version=$(python3 -c 'import json; print(json.load(open("tests/kernels/stable.json"))["version"])')
# A fixed location lets CI cache the built tree; DKMS needs it as /lib/modules/*/build.
cache=/var/cache/tbnet-upstream-kernel
tree=$cache/linux-$version
# Reuse a tree only if it was completed from the same lock, config and compiler.
inputs=$( (cat tests/kernels/stable.json tests/kernels/config; gcc --version | head -1) | sha256sum | cut -d ' ' -f 1)
if test -f "$tree/.tbnet-built" && test "$(cat "$tree/.tbnet-built")" = "$inputs"; then
    echo "Reusing the cached test kernel tree for Linux $version"
    cd "$tree"
else
    rm -rf "$cache"
    mkdir -p "$cache"
    download=$(mktemp -d /tmp/tbnet-upstream-kernel.XXXXXX)
    trap 'rm -rf "$download"' EXIT
    python3 - "$download" <<'PY'
import hashlib,json,pathlib,sys,urllib.request
lock=json.loads(pathlib.Path('tests/kernels/stable.json').read_text())
p=pathlib.Path(sys.argv[1])/'linux.tar.xz'
with urllib.request.urlopen(lock['url'],timeout=180) as r, p.open('wb') as f:
    while data:=r.read(1024*1024):f.write(data)
assert hashlib.sha256(p.read_bytes()).hexdigest()==lock['sha256'], 'kernel checksum mismatch'
PY
    tar -xJf "$download/linux.tar.xz" -C "$cache"
    rm -rf "$download"
    cd "$tree"
    make KCONFIG_ALLCONFIG="$root/tests/kernels/config" allnoconfig
    # The stock network driver is needed to verify DKMS removal restores it.
    grep -qx 'CONFIG_USB4_NET=m' .config || {
        echo 'Test kernel must build the original thunderbolt_net module'
        exit 1
    }
    # nproc follows the CPUs this container may run on, so larger runners build faster.
    jobs=$(nproc)
    echo "Building the test kernel with $jobs parallel jobs"
    make -j"$jobs" bzImage modules > "$root/build/upstream-kernel/build.log" 2>&1 || {
        tail -100 "$root/build/upstream-kernel/build.log"
        exit 1
    }
    echo "$inputs" > .tbnet-built
fi
kernel=$(make -s kernelrelease)
make modules_install > "$root/build/upstream-kernel/install.log" 2>&1
mkdir -p /boot
cp arch/x86/boot/bzImage "/boot/vmlinuz-$kernel"
cp .config "$root/build/upstream-kernel/kernel.config"
cd "$root"
make check
python3 scripts/qemu-test.py --kernel "$kernel"
# Real DKMS build/install/remove against the same complete, matching kernel.
package_version=$(cat VERSION)
source_dir="/usr/src/thunderbolt-net-$package_version"
mkdir -p "$source_dir/src"
cp VERSION Makefile dkms.conf "$source_dir/"
cp src/Makefile src/*.c src/*.h "$source_dir/src/"
original=$(modinfo -k "$kernel" -F filename thunderbolt_net)
original_hash=$(sha256sum "$original" | cut -d ' ' -f 1)
dkms add -m thunderbolt-net -v "$package_version"
dkms install -m thunderbolt-net -v "$package_version" -k "$kernel"
selected=$(modinfo -k "$kernel" -F filename thunderbolt_net)
case "$selected" in */updates/dkms/*) ;; *) echo 'DKMS module was not selected'; exit 1;; esac
test "$(modinfo -k "$kernel" -F version thunderbolt_net)" = "$package_version"
bash scripts/check-throttling.sh "$kernel" "$selected" --require
dkms remove -m thunderbolt-net -v "$package_version" --all
test "$(modinfo -k "$kernel" -F filename thunderbolt_net)" = "$original"
test "$(sha256sum "$original" | cut -d ' ' -f 1)" = "$original_hash"
echo "Validated upstream kernel and DKMS restoration: $kernel"
