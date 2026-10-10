#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Run in a disposable container. Never bind-mount a host /lib/modules or /usr/src.
set -euo pipefail
cd "$(dirname "$0")/.."
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=3 update -qq
apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=3 install -y --no-install-recommends build-essential debhelper dh-dkms dkms \
    python3 python3-yaml git lintian shellcheck kmod busybox-static iproute2 \
    qemu-system-x86 cpio zstd xz-utils ca-certificates
# shellcheck source=/dev/null
. /etc/os-release
case "$ID" in
    ubuntu) apt-get install -y --no-install-recommends linux-headers-generic ;;
    debian) apt-get install -y --no-install-recommends linux-headers-amd64 linux-image-amd64 ;;
    *) echo 'Unsupported CI distribution' >&2; exit 1 ;;
esac
kernel=$(find /lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -V | tail -n 1)
test -n "$kernel"
test -d "/lib/modules/$kernel/build"
if test "$ID" = ubuntu; then
    # Select the matching image directly; the generic image metapackage also
    # pulls large, unrelated hardware-firmware bundles into a diskless CI guest.
    image_packages=("linux-image-$kernel")
    if apt-cache show "linux-modules-extra-$kernel" >/dev/null 2>&1; then
        image_packages+=("linux-modules-extra-$kernel")
    fi
    apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=3 install -y --no-install-recommends \
        "${image_packages[@]}"
fi
make check
shellcheck scripts/*.sh
dpkg-buildpackage --build=binary --no-sign
version=$(cat VERSION)
deb="../thunderbolt-net-dkms_${version}-1_all.deb"
python3 scripts/audit-deb.py "$deb"
lintian --fail-on error "$deb"
python3 scripts/qemu-test.py --kernel "$kernel"
if test "$ID" = ubuntu && test "$VERSION_ID" = 26.04; then
    python3 tests/rx-lifecycle/run.py --kernel "$kernel"
    python3 tests/rx-lifecycle/run.py --kernel "$kernel" --mode negative
fi

# Actual package lifecycle, isolated from the host's module tree and kernel.
test ! -d "/var/lib/dkms/thunderbolt-net/$version"
original=$(modinfo -k "$kernel" -F filename thunderbolt_net)
original_hash=$(sha256sum "$original" | cut -d ' ' -f 1)
dpkg -i "$deb"
dkms install -m thunderbolt-net -v "$version" -k "$kernel"
selected=$(modinfo -k "$kernel" -F filename thunderbolt_net)
case "$selected" in */updates/dkms/*) ;; *) echo "DKMS override not selected: $selected" >&2; exit 1 ;; esac
test "$(modinfo -k "$kernel" -F version thunderbolt_net)" = "$version"
bash scripts/check-throttling.sh "$kernel" "$selected"
test -z "$(find /lib/modules/"$kernel" -name 'tbnet_*test.ko*' -print -quit)"
defaults='options thunderbolt_net rx_segment=1 rx_segment_mtu=1500 rx_page_pool=1'
modprobe --showconfig | grep -qxF "$defaults" || { echo 'Package defaults not visible to modprobe' >&2; exit 1; }
dpkg --purge thunderbolt-net-dkms
test ! -e /usr/lib/modprobe.d/thunderbolt-net.conf
restored=$(modinfo -k "$kernel" -F filename thunderbolt_net)
test "$restored" = "$original"
test "$(sha256sum "$restored" | cut -d ' ' -f 1)" = "$original_hash"
test ! -d "/var/lib/dkms/thunderbolt-net/$version"
echo 'Debian install/remove and original-module restoration passed'

mkdir -p dist
cp "$deb" dist/
python3 scripts/make-dist.py
(
    cd dist
    sha256sum ./*.deb ./*.tar.gz > SHA256SUMS
)
printf 'Validated kernel: %s\n' "$kernel"
