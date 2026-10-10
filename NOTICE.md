# Source provenance and attribution

`src/main.c`, `src/trace.c`, and `src/trace.h` derive from the Linux v7.2.9
`drivers/net/thunderbolt` sources (stable commit `5fce161649b4`), licensed
GPL-2.0. Original author and copyright notices are preserved. Unmodified
baseline copies are kept under `upstream/`, with SHA-256 checksums and public
source URLs in `upstream/provenance.json`.

- [Linux v7.2.9 driver](https://github.com/gregkh/linux/tree/5fce161649b4d779d1b76d9fcd52dc77779774b8/drivers/net/thunderbolt)

The move from the v7.0 baseline was imported as individual upstream commits
that keep their original author, date and message, with an `Upstream-commit`
trailer. They are the work of their authors, not of this project:

- Mika Westerberg: move `ring_frame_size()` to `thunderbolt.h`
  ([5140737c592d](https://github.com/gregkh/linux/commit/5140737c592d23b4de0f98c47dd347684a0c8de3))
  and let service drivers configure interrupt throttling
  ([c51777370ac2](https://github.com/gregkh/linux/commit/c51777370ac2ef435401340e205ef1d0c778df28)).
- Maoyi Xie: bound `frame_count` to prevent a `frags[]` overflow
  ([55d9895f8997](https://github.com/gregkh/linux/commit/55d9895f89970501fe126d1026b586b04a224c27)).
  Earlier versions carried a minimal backport of this fix.
- Fan Ye: revert TX end-to-end flow control
  ([1881f2efbf7f](https://github.com/gregkh/linux/commit/1881f2efbf7f78dc0a79a387b29fde6ff56d3731)),
  release a mismatched Rx HopID
  ([1c361f6cf39b](https://github.com/gregkh/linux/commit/1c361f6cf39be7cc0ce37c0b67bd1cdf74b0a0c1)),
  mark the connection down when bring-up fails
  ([d6c0af293129](https://github.com/gregkh/linux/commit/d6c0af293129345a17dc31c9b4179dc7f3d6af7a))
  and count delivered packets in RX statistics
  ([21c0a41260e4](https://github.com/gregkh/linux/commit/21c0a41260e4b799a89ef34ae1b67261cbbcdfb1)).
- Fan XinRan: tear down DMA paths before stopping the rings
  ([68bf02b6b4ad](https://github.com/gregkh/linux/commit/68bf02b6b4ad3f748c6db71fd77b6c0402d252f4)).

Version 0.2.0 carried backports of the three connection-cleanup fixes; they are
now part of the baseline. Hunks in the Thunderbolt core and headers are not
part of this module and remain a dependency on the host kernel.

Project changes add `rx_fixup.c/.h`, module parameters, validation counters,
the receive integration, an Ethernet-header-length/headroom correction for GRO,
version metadata, older-kernel compatibility for the speed constant and
frame-size helper (`src/compat.h`), build-time detection of the ring-throttling
API (`src/Makefile`), tests and packaging. The workaround
is experimental and has not been represented as an accepted upstream patch.
Use `diff -u upstream/main.c src/main.c` to review the driver integration; the
additional RX implementation is separate.

Package maintenance and new contributions: Shun Li and project contributors.
No endorsement by upstream authors or vendors is implied.
