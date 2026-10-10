# Upstream maintenance

The `feature/upstream-tracking` branch starts from the released v0.2.0 mainline,
not the automatic-MTU experiment. Its reviewed baseline is Linux **v7.2.9**,
commit `5fce161649b4d779d1b76d9fcd52dc77779774b8` in `gregkh/linux`.
This is development work, not a new published release or a change to v0.2.0.

## What is tracked

- `upstream/`: unmodified `main.c`, `trace.c` and `trace.h`, with immutable URLs,
  SHA-256 digests and attribution in `provenance.json`.
- `upstream/commits/`: byte-for-byte original upstream mail patches, including
  changes outside this DKMS module, with SHA-256 digests in `provenance.json`.
  Original authors, author dates, full messages and sign-offs are retained in
  individual Git imports. `Upstream-commit` records each original full SHA;
  `Import-scope` states the path mapping and excluded controller/header scope.
- `src/`: the maintained driver, including our separate RX implementation.
  Local integration and compatibility changes are separate commits.
- `upstream/local.patch`: the exact local delta for those three upstream files.
  `make check` replays it in a temporary directory and compares the result with
  `src/`. It must be refreshed deliberately after reviewing changes.
- `tests/kernels/stable.json`: the exact upstream kernel archive and checksum
  used for compatibility CI. Updating driver files does not update this lock,
  the DKMS version gate, or the host's Thunderbolt controller driver.

Linux 7.2.9 incorporates the previously imported connection cleanup and RX
fragment-bound fixes. The remaining local delta retains RX normalization,
page-pool ownership, startup/teardown serialization and the GRO header fix.
The new baseline also corrects complete-packet RX statistics and TX/RX E2E
flag placement, and requests 128-microsecond ring interrupt throttling where
supported. These changes need hardware regression before release.

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
enabled. Arch's matching `linux-headers`/`linux-lts-headers` and its kernel config
remain separate from an upstream-source build; this project has no Arch package
or Arch DKMS lifecycle gate yet.

## Check and prepare an update

Python 3, Git and outbound HTTPS are needed for discovery/preparation. Normal
DKMS compilation never downloads source. Run from the project root:

```sh
python3 scripts/upstream.py status
python3 scripts/upstream.py prepare --tag v7.2.9 --output build/candidate-v7.2.9
```

The second command checks the unchanged current baseline. For a new release,
select its original upstream commits in dependency order and pass each full SHA:

```sh
python3 scripts/upstream.py prepare --tag <stable-tag> \
  --commit <first-full-upstream-sha> --commit <next-full-upstream-sha> \
  --output build/upstream-candidate
```

The tool downloads the **original commit mail patches**, then uses `git am` in a
separate candidate Git repository. Only diff path names are remapped from
`drivers/net/thunderbolt/` to `upstream/`; code hunks and original author/message
information are not rewritten. Raw patches are archived unchanged. A fresh
endpoint download is used only to verify that the selected series reproduces
the target baseline exactly. Missing commits cannot be hidden by replacing
files with a downloaded snapshot. Nonlinear stable-to-mainline transitions or
already-backported fixes can conflict and need explicit review.

Output must not already exist. A failed upstream application leaves the
candidate's `git am` state for inspection. After successful imports, local
changes are three-way merged into candidate `src/`; conflicts remain there and
produce a nonzero exit. The existing checkout is never overwritten. Neither
clean application nor identical final bytes proves semantic compatibility.

`review.json` records `import_base` and `import_head`. Transfer that range with
`git format-patch` and `git am` so the individual authors and messages survive;
**do not copy the final upstream files and squash them into your own commit**:

```sh
git -C build/upstream-candidate format-patch --stdout \
  <import_base>..<import_head> > build/upstream-imports.mbox
git am build/upstream-imports.mbox
```

Review controller dependencies and the carried historical attribution. Commit
candidate `upstream/commits/` and `upstream/provenance.json` as maintenance
metadata. Resolve and integrate candidate `src/` in separate local commits;
review/update extracted RX fingerprints when functions change, then regenerate
the local delta and update the release allowlist:

```sh
python3 scripts/upstream.py refresh
make check
```

Imported commit IDs in this repository necessarily differ because their parent
trees and paths differ. `Upstream-commit`, the unchanged raw mail and its digest
preserve the original identity and attribution. Controller and header hunks in
cross-subsystem commits remain in the host kernel, not in the DKMS payload; the
complete archived patch makes that dependency visible. Preserve these import
commits when merging a PR; do not squash away the upstream authorship.

Update the kernel lock from the official release and its published checksum,
and adjust the version gate only with matching-header test evidence. Keep
source import, compatibility fixes, tests and documentation in reviewable
commits. Before release, assign a new package version and run the installation,
rollback and hardware checks; do not publish development artifacts as v0.2.0.

## CI and update discovery

Pushes, pull requests and manual workflow dispatch run the existing distribution
matrix plus a checksum-pinned upstream stable kernel. The new job builds the
kernel in a disposable container, boots packet/bridge/router/GRO tests in a
no-NIC QEMU guest, and checks actual DKMS installation/removal and restoration
of the kernel's original module. It is a release prerequisite. This does not
exercise physical enumeration, NHI DMA, macOS or real throughput.

An advisory job compares the baseline with kernel.org's latest stable and
reports the current mainline candidate in the Actions summary. A newer release
is a maintenance signal, not automatic acceptance. Discovery/network failure
is visible but does not change pinned release inputs. There is no unattended
source replacement, PR creation, merge, release or separate monitoring task.
Run the workflow manually to check between code changes.

References: [kernel.org releases](https://www.kernel.org/releases.json),
[stable source](https://github.com/gregkh/linux/tree/5fce161649b4d779d1b76d9fcd52dc77779774b8/drivers/net/thunderbolt),
[controller API](https://github.com/gregkh/linux/blob/5fce161649b4d779d1b76d9fcd52dc77779774b8/include/linux/thunderbolt.h).
