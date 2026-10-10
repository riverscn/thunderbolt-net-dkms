# Upstream maintenance

Version 0.3.0 starts from the released v0.2.0 mainline, not the automatic-MTU
experiment. Its reviewed baseline is Linux **v7.2.9**, commit
`5fce161649b4d779d1b76d9fcd52dc77779774b8` in `gregkh/linux`. It is unreleased
and does not change the published v0.2.0.

## What is tracked

- `upstream/`: unmodified `main.c`, `trace.c` and `trace.h`, with immutable URLs,
  SHA-256 digests and attribution in `provenance.json`.
- Git history: each upstream import retains its original author, author date,
  full message and sign-offs. `Upstream-commit` identifies the original commit;
  `Import-scope` records the path mapping and excluded controller/header scope.
  `provenance.json` lists the imported SHAs. No patch-file archive is maintained.
- `src/`: the maintained driver, including our separate RX implementation.
  Local integration and compatibility changes are separate commits.
- `upstream.py diff`: derives the local delta from `upstream/` and `src/` on
  demand. `make check` replays this derived delta in a temporary directory;
  normal compilation and offline checks do not need a Linux Git clone.
- `tests/kernels/stable.json`: the exact upstream kernel archive and checksum
  used for compatibility CI. Updating driver files does not update this lock,
  the DKMS version gate, or the host's Thunderbolt controller driver.

Linux 7.2.9 incorporates the previously imported connection cleanup and RX
fragment-bound fixes. The remaining local delta retains RX normalization,
page-pool ownership, startup/teardown serialization and the GRO header fix.
The new baseline also corrects complete-packet RX statistics and TX/RX E2E
flag placement, and requests 128-microsecond ring interrupt throttling where
supported. These changes need hardware regression before release.

## Performance expectations and validation

No hardware performance gain over v0.2.0 has been established for this baseline.
The upstream changes have different purposes:

| Change | Expected effect and limits |
| --- | --- |
| Ring interrupt throttling | The old core already programmed a fixed 128 microseconds. The new API moves that responsibility to service drivers, and this driver requests the same interval. This preserves behavior rather than adding a new optimization. On Linux 7.2.9, a control build without the call had 11–44× more interrupts per GiB and about 20% lower download throughput, with about 0.1 ms lower ping latency ([measurements](upstream-validation.md#new-core-api-path-on-linux-729)). |
| TX E2E disabled, RX E2E retained when negotiated | May restore transmission on controllers that stall waiting for end-to-end credits. The upstream report concerns affected hardware, not evidence of faster throughput on our working setup. |
| Complete-packet RX accounting | Corrects packet/byte statistics. Changed counters are not evidence of higher application throughput. |
| Frame-size helper and compatibility wrapper | API adaptation with no established performance improvement. |

See the original [throttling commit](https://github.com/gregkh/linux/commit/c51777370ac2ef435401340e205ef1d0c778df28)
and [TX E2E revert](https://github.com/gregkh/linux/commit/1881f2efbf7f78dc0a79a387b29fde6ff56d3731).

Hardware regression should compare the candidate against v0.2.0 on the same
kernel, controller, peer, MTU and offload settings. Measure bidirectional iperf3
throughput and retransmissions, CPU/interrupt load, and latency; exercise bridge
forwarding and reconnection during traffic. Confirm the route is Thunderbolt
and exclude local-tunnel effects. Test the new-core API path as well as an
older-core fallback. CI and QEMU do not establish physical DMA behavior or
performance. Our merge recommendation is to complete this regression first;
it is also required before declaring the new baseline hardware-validated.

The [upstream validation](upstream-validation.md) covers both paths on one
controller and peer: the older-core fallback against v0.2.0 on Proxmox 7.0, and
the new-core API on Linux 7.2.9 with the controller passed through to a VM.
Physical disconnect during traffic, suspend/resume and long stress remain open.

## Compatibility decisions

The frame-size helper uses the native core API when its associated public size
constant is available; old cores retain the equivalent 4096-byte framing rule.
Ring throttling requires both a declaration in the target headers and an
export in that kernel's `Module.symvers`. The complete module build still
checks types and symbols. Older cores retain their existing moderation
behavior; no replacement controller implementation or fake success stub is
installed. This handles vendor backports better than assuming a version number
alone proves that an API is present.

The DKMS gate permits x86-64 kernels in the existing 6.8–6.19 range and 7.0–7.2.
That gate is not an exhaustive support claim. The CI matrix tests exact selected
kernels; mainline release candidates and future kernel families are not silently
enabled. Arch's own kernel build and `linux-headers` layout are tested separately
from the upstream-source build (see below); this project still has no Arch
package, and `linux-lts` is not tested.

## Check and prepare an update

Maintain a separate trusted Linux stable Git clone. Fetch the desired tag and
history there; never add the full Linux history to this small DKMS repository.
For example (initial cloning may take time):

```sh
git clone --filter=blob:none --no-checkout \
  https://github.com/gregkh/linux.git ../linux-upstream
git -C ../linux-upstream fetch origin tag v7.2.9
python3 scripts/upstream.py status
python3 scripts/upstream.py prepare --linux-tree ../linux-upstream \
  --tag v7.2.9 --output build/upstream-candidate
```

The example checks the current baseline. Select a newly reviewed stable tag
for an update. The tool resolves it from the **local Git repository**, verifies
that its old commit tree matches the checked-in baseline, and enumerates
relevant commits in topological order. It reads original author, date, message
and changes from Git objects. For path mapping it streams `git format-patch`
into `git am` internally; no patch files are downloaded or stored. Partial
clones may fetch missing objects on demand; complete clones work offline.

For a divergent stable-to-mainline transition, review the original history and
supply full commit SHAs explicitly in application order:

```sh
python3 scripts/upstream.py prepare --linux-tree ../linux-upstream \
  --tag <stable-tag> --commit <first-full-sha> --commit <next-full-sha> \
  --output build/upstream-candidate
```

Shallow repositories need enough history, including commit parents. Merge
commits cannot be imported as ordinary commits; merge-only changes or a missing
commit cause the final tree comparison to fail. The selected series must
reproduce the target Git tree byte for byte. The tool does not silently replace
files with a snapshot or guess equivalence of backported commits.

The candidate is a separate Git repository and its output path must not exist.
A failed application leaves `git am` state there. After imports, the tool
three-way merges the local changes into candidate `src/`, retaining conflicts
for review. It never writes into the original checkout or changes the Linux
clone's refs. Identical final bytes are not proof of semantic compatibility.

`review.json` records `import_base` and `import_head`. Transfer these individual
commits through Git (replace the placeholders with the recorded SHAs):

```sh
git fetch ./build/upstream-candidate HEAD
git cherry-pick <import_base>..<import_head>
```

Commit the candidate provenance metadata separately. Review controller
requirements, integrate candidate `src/` in local commits, update lifecycle
fingerprints when functions change, then inspect the derived delta and test:

```sh
python3 scripts/upstream.py diff
make check
```

Local import IDs differ because parent trees and paths differ. `Upstream-commit`
and the source repository identify the original full commit for `git show`,
including controller/header hunks excluded from the DKMS module. Those changes
remain a host-kernel dependency, with old-kernel compatibility handled locally.
Preserve the individual import commits when merging; do not squash away their
authorship. Treat source-import, compatibility, test and documentation changes
as separate reviewable commits.

Update the kernel lock from the official release and its published checksum,
and adjust the version gate only with matching-header test evidence. Keep
source import, compatibility fixes, tests and documentation in reviewable
commits. Before release, assign a new package version and run the installation,
rollback and hardware checks; never republish changed artifacts under an
existing version.

## CI and update discovery

Pushes to `main`, release tags, pull requests and manual workflow dispatch run
the existing distribution matrix plus a checksum-pinned upstream stable kernel.
Other branches run through their pull request, which tests the merge result; to
check a branch without one, run `gh workflow run ci.yml --ref <branch>`.
The upstream job builds the kernel in a disposable container, boots packet/bridge/router/GRO tests in a
no-NIC QEMU guest, and checks actual DKMS installation/removal and restoration
of the kernel's original module. It is a release prerequisite. `scripts/check-throttling.sh` also
checks that each built module uses `tb_ring_throttling()` exactly when its kernel
provides it, and the pinned job requires the new API, so a detection failure
cannot fall back silently. This does not exercise physical enumeration, NHI DMA,
macOS or real throughput.

A separate job runs in an Arch Linux container with the `linux` and
`linux-headers` packages pinned by URL and SHA-256 in `tests/kernels/arch.json`
(Arch Linux Archive). It runs the same QEMU suite on that distribution kernel,
installs and removes the module with Arch's `dkms`, and requires the new
throttling API. Other packages in that container follow the rolling repository,
so a toolchain update can change results without a change here; update the lock
deliberately, like the upstream kernel lock.

An advisory job compares the baseline with kernel.org's latest stable and
reports the current mainline candidate in the Actions summary. A newer release
is a maintenance signal, not automatic acceptance. Discovery/network failure
is visible but does not change pinned release inputs. There is no unattended
source replacement, PR creation, merge, release or separate monitoring task.
Run the workflow manually to check between code changes.

References: [kernel.org releases](https://www.kernel.org/releases.json),
[stable source](https://github.com/gregkh/linux/tree/5fce161649b4d779d1b76d9fcd52dc77779774b8/drivers/net/thunderbolt),
[controller API](https://github.com/gregkh/linux/blob/5fce161649b4d779d1b76d9fcd52dc77779774b8/include/linux/thunderbolt.h).
