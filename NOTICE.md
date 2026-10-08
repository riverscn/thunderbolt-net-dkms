# Source provenance and attribution

`src/main.c`, `src/trace.c`, and `src/trace.h` derive from the Linux v7.0
`drivers/net/thunderbolt` sources, licensed GPL-2.0. Original author and copyright
notices are preserved. Unmodified baseline copies are kept under `upstream/`,
with SHA-256 checksums and public source URLs in `upstream/provenance.json`.

- [Linux v7.0 driver](https://github.com/torvalds/linux/tree/v7.0/drivers/net/thunderbolt)
- [Upstream fragment-count fix, 55d9895f8997](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/commit/?id=55d9895f8997)

The fragment-count check changes the maximum from a ring-derived bound to
`MAX_SKB_FRAGS + 1`. This is a minimal backport of work attributed to Maoyi Xie,
separate from the receive GSO workaround. It must not be presented as original
work of this project or as an upstream TSO fix.

Project changes add `rx_fixup.c/.h`, module parameters, validation counters,
the receive integration, version metadata, an older-kernel speed-constant
compatibility definition, tests and packaging. The workaround
is experimental and has not been represented as an accepted upstream patch.
Use `diff -u upstream/main.c src/main.c` to review the driver integration; the
additional RX implementation is separate.

Package maintenance and new contributions: Shun Li and project contributors.
No endorsement by upstream authors or vendors is implied.
