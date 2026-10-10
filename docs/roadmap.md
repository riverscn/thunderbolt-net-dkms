# Development roadmap

## Current scope

Version 0.2.0 retains the Linux v7.0 driver baseline, the oversized-TCP RX
workaround and the 0.1.1 GRO header correction. It adds optional RX page
recycling, lifecycle serialization and three attributed connection cleanup
backports. See [design](rx-page-pool.md) and [compatibility](compatibility.md).

## Next iteration

- Select and pin a newer stable upstream driver revision; record its source
  URLs and hashes. Review dependencies on Thunderbolt core before importing it.
- Add a focused compatibility layer for selected older kernels. Distribution
  backports make version numbers alone insufficient evidence of API support.
- Validate Linux 7.2 and then assess Arch packaging. Neither is supported by
  the current build gate.
- Expand hardware coverage to more controllers, suspend/resume and longer
  workloads. Track enumeration failures separately from packet processing.

Keep RX normalization separate from upstream-derived code. Never replace a
missing kernel dependency with a no-op merely to make a build pass. DKMS
installation must not download a moving upstream branch, and updating this
network module must not imply an update to the kernel's controller driver.

A future iteration must include matching-header builds, guest regression tests,
package install/remove checks, hardware throughput in both directions, payload
integrity, and recovery testing for its supported configurations.
