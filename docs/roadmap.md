# Upstream tracking and development roadmap

## Initial development baseline

Version 0.1.0 preserves the validated RX normalization prototype, Linux v7.0
driver baseline, source-only DKMS packaging, Debian package and current tests.
The next milestone described here is not implemented in this version. In
particular, the current DKMS configuration does not permit Linux 7.2 builds.

Version 0.1.1 adds the GRO header-length correction and ordering regression tests
while retaining that driver baseline and compatibility scope.

As checked on 2026-10-08, the latest upstream stable kernel is 7.2.9 and the
Arch Linux `linux` package is `7.2.9.arch1-1`. These are dated observations, not
permanent version requirements. Sources: [kernel.org](https://www.kernel.org/)
and [Arch package metadata](https://archlinux.org/packages/core/x86_64/linux/).

## Next milestone: stable upstream with backward compatibility

Use a pinned current stable upstream driver as the baseline; the candidate at
the time of this plan is v7.2.9. Record the exact tag, source URLs and hashes.
Review driver changes and related Thunderbolt core dependencies before importing
them. Do not download a moving upstream branch during DKMS installation or
automatically release an unreviewed rebase.

Keep the upstream-derived driver close to upstream. Retain the RX normalization
logic in separate files with a small integration patch. Put older-kernel API
adaptation in a focused compatibility layer instead of scattering version
conditionals through the receive implementation.

Prefer checks of available target-kernel interfaces and exports where practical:
distribution kernels can backport APIs independently of their version number.
Simple constants or helpers can use equivalent fallback implementations. A
feature requiring newer Thunderbolt core behavior needs a dependency review;
either preserve the older kernel's documented behavior or reject that target.
Do not add fake success or empty stubs solely to make a build pass. Updating this
network module does not update the kernel's Thunderbolt controller driver.

The v7.2.9 comparison identified changes to DMA teardown order, connection-failure
cleanup, receive statistics, frame-size helpers and ring throttling. Review and
test these together with their dependencies. They are separate from this
project's RX GSO workaround; retain upstream attribution and do not describe
them as new project fixes. Reference:
[stable driver source](https://github.com/gregkh/linux/blob/v7.2.9/drivers/net/thunderbolt/main.c).

## Validation and packaging targets

The initial candidate families are x86-64 Linux 6.8, 6.12, 6.18, 7.0 and 7.2.
Select concrete distribution kernel/header pairs, including Proxmox and Arch;
do not infer support for every intermediate or vendor kernel from this list.
Only expand the DKMS build gate after the corresponding checks pass.

- Keep Debian source packaging and add an Arch `PKGBUILD` using the same DKMS
  source and version. Arch packaging and any AUR publication remain future work.
- Add Arch's stable kernel and matching headers to CI, and maintain explicit
  tests for the selected older kernels and Proxmox target.
- Run module compilation, packet/forwarding tests in the target kernel, and
  package installation/removal with verification of stock-module restoration.
- Review version-dependent fallbacks and exercise them on the relevant kernels.
- Repeat hardware throughput, payload-integrity and hotplug testing after the
  driver rebase. Previous v7.0 measurements do not validate the new baseline.
- Distinguish build, software-path, package-lifecycle and hardware results in
  documentation. Keep unsupported targets and missing tests explicit.

## Ongoing maintenance

Review new stable releases for relevant driver, Thunderbolt core and networking
changes. Incorporate bug/security fixes promptly after dependency review and
regression testing. Avoid unnecessary rebases when relevant code is unchanged.
Keep release candidates out of the supported baseline until deliberately tested.

Preserve source provenance, the release allowlist and privacy checks throughout
each rebase. Publish a new project version for changed driver behavior or
compatibility; do not overwrite an existing release. Upstream acceptance of a
properly reviewed RX fix remains the preferred long-term outcome.
