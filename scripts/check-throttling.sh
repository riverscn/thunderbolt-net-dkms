#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Verify that a built module took the ring-throttling path its kernel supports.
# usage: check-throttling.sh KERNEL_RELEASE MODULE [--require]
# The expectation mirrors src/Makefile: declaration in the target headers and
# an export in that kernel's Module.symvers. --require also fails when the
# kernel itself lacks the API, so a pinned new kernel cannot silently fall back.
set -euo pipefail
kernel=$1
module=$2
require=${3:-}
build=/lib/modules/$kernel/build
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# Ask Kbuild for the trees src/Makefile sees. Split header packages (Debian's
# -common/-arch layout) keep include/linux outside the build directory.
mkdir "$tmp/probe"
# shellcheck disable=SC2016 # Make variables, expanded by Kbuild.
printf '$(info TBNET_TREES $(srctree) $(objtree))\n' > "$tmp/probe/Makefile"
trees=$(make -s -C "$build" M="$tmp/probe" modules 2>/dev/null | grep -m1 '^TBNET_TREES ' || true)
read -r _ srctree objtree <<< "$trees"
if test -z "${srctree:-}" || test -z "${objtree:-}"; then
    echo "Kbuild did not report its source and object trees for $kernel" >&2
    exit 1
fi
case "$srctree" in /*) ;; *) srctree=$build/$srctree ;; esac
case "$objtree" in /*) ;; *) objtree=$build/$objtree ;; esac
header=$srctree/include/linux/thunderbolt.h
test -r "$header" || { echo "Missing $header" >&2; exit 1; }
expected=no
if grep -q 'int tb_ring_throttling' "$header" &&
    grep -q '[[:space:]]tb_ring_throttling[[:space:]]' "$objtree/Module.symvers"; then
    expected=yes
fi
case "$module" in
    *.zst) zstd -q -d "$module" -o "$tmp/m.ko" ;;
    *.xz) xz -d -c "$module" > "$tmp/m.ko" ;;
    *.gz) gzip -d -c "$module" > "$tmp/m.ko" ;;
    *) cp "$module" "$tmp/m.ko" ;;
esac
actual=no
# Capture first: under pipefail, grep -q exiting early can SIGPIPE nm.
undefined=$(nm -u "$tmp/m.ko")
if grep -qw tb_ring_throttling <<< "$undefined"; then
    actual=yes
fi
echo "ring throttling for $kernel: kernel API $expected, module uses $actual"
if test "$expected" != "$actual"; then
    echo 'Ring-throttling detection does not match the target kernel' >&2
    exit 1
fi
if test "$require" = --require && test "$expected" != yes; then
    echo 'Target kernel must provide tb_ring_throttling' >&2
    exit 1
fi
