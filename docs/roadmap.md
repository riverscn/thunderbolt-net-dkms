# Development roadmap

## Current scope

Version 0.2.0 retains the Linux v7.0 driver baseline, the oversized-TCP RX
workaround and the 0.1.1 GRO header correction. It adds optional RX page
recycling, lifecycle serialization and three attributed connection cleanup
backports. See [design](rx-page-pool.md) and [compatibility](compatibility.md).

## Current development branch

`feature/upstream-tracking` selects Linux 7.2.9 and adds a reproducible update
procedure, older-kernel compatibility and pinned upstream-kernel testing.
See [upstream maintenance](upstream-tracking.md).

## Before the next release

- The new baseline's hardware regression is recorded in
  [upstream validation](upstream-validation.md). Still open: physical disconnect
  during traffic, suspend/resume, long stress, Linux 7.2 bridge forwarding and
  more controllers. Watch the higher retransmission counts seen in one phase.
- Assess Arch packaging and matching distribution kernels independently.
- Review mainline RC changes as early warnings; do not widen support gates
  without builds and tests.

Keep RX normalization separate from upstream-derived code. Never replace a
missing kernel dependency with a no-op merely to make a build pass. DKMS
installation must not download a moving upstream branch, and updating this
network module must not imply an update to the kernel's controller driver.

A future iteration must include matching-header builds, guest regression tests,
package install/remove checks, hardware throughput in both directions, payload
integrity, and recovery testing for its supported configurations.
