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
expected=no
if grep -q 'int tb_ring_throttling' "$build/include/linux/thunderbolt.h" &&
    grep -q '[[:space:]]tb_ring_throttling[[:space:]]' "$build/Module.symvers"; then
    expected=yes
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
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
