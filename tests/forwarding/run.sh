#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Disposable CI container only. Builds a test kernel, never boots the host into it.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=3 update -qq
apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=3 install -y --no-install-recommends \
    build-essential bc bison flex libssl-dev libelf-dev python3 kmod \
    busybox-static iproute2 qemu-system-x86 cpio zstd xz-utils curl ca-certificates patch
mkdir -p "$root/build/forwarding"
build=$(mktemp -d /tmp/tbnet-forward-kernel.XXXXXX)
trap 'rm -rf "$build"' EXIT
curl --fail --location --max-time 180 --retry 2 \
    https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.0.tar.xz \
    -o "$build/linux.tar.xz"
(cd "$build" && echo 'bb7f6d80b387c757b7d14bb93028fcb90f793c5c0d367736ee815a100b3891f0  linux.tar.xz' | sha256sum -c -)
tar -xJf "$build/linux.tar.xz" -C "$build"
cd "$build/linux-7.0"
patch --batch --fuzz=0 -p1 < "$root/tests/forwarding/linux-7.0-rx-gso-mtu.patch"
make KCONFIG_ALLCONFIG="$root/tests/forwarding/kernel.config" allnoconfig
make -j2 bzImage modules > "$root/build/forwarding/build.log" 2>&1 || {
    tail -100 "$root/build/forwarding/build.log"
    exit 1
}
kernel=$(make -s kernelrelease)
make modules_install > "$root/build/forwarding/install.log" 2>&1
mkdir -p /boot
cp arch/x86/boot/bzImage "/boot/vmlinuz-$kernel"
cp .config "$root/build/forwarding/kernel.config"
cd "$root"
python3 scripts/qemu-test.py --kernel "$kernel"
echo "Validated experimental core kernel: $kernel"
