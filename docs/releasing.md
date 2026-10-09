# Release procedure

1. Review `NOTICE.md`, kernel compatibility, known limitations, and the public
   maintainer identity. Set the repository description and GPL-2.0 license.
2. Enable branch protection for `main` and require the source and all Linux CI
   jobs. Enable GitHub private vulnerability reporting if available.
3. Keep `VERSION`, `dkms.conf`, and `debian/changelog` in sync. The current package
   uses upstream version `0.1.1` and Debian revision `-1`.
4. Review `release-files.txt`; every tracked file must appear there. Run source
   checks and all container tests. Keep private audit deny-lists outside Git.
5. Inspect the `.deb` with `dpkg-deb --contents` and the source archive with
   `tar -tzf`. Run `scripts/audit-deb.py` on the exact release artifact.
6. Push reviewed source to the repository's `main` branch. Inspect the actual
   GitHub Actions run; configuring CI locally does not mean a hosted run passed.
7. Preview `python3 scripts/release-notes.py`. **Every release, including
   prereleases, must start with a prominent installation/upgrade guide**, before
   the change list. Keep version-specific download links, checksum verification,
   prerequisite `apt update` / `apt install` commands (separate distribution
   and Proxmox headers), installation, opt-in configuration, activation/verification,
   and a link to the full installation/rollback guide in that opening section.
   Review this generated text when installation requirements change.
8. Create and push a tag matching `v$(cat VERSION)` using your intended public
   author identity. The workflow verifies the tag/version and all jobs before
   creating a prerelease with `.deb`, source tarball and `SHA256SUMS`. It generates
   installation-first notes followed by only that version's changelog entry;
   do not replace the notes with the entire changelog or GitHub auto-generated
   notes. Manual edits to a published release must retain the opening guide.

Do not retag or overwrite a released version. Publish a new version for changes.
Keep experimental releases marked prerelease until hardware and stability
coverage justify a different status. Runtime hardware tests are not performed
on public CI runners.

The source archive normalizes ordering, uid/gid, permissions and timestamps.
Its timestamp follows `SOURCE_DATE_EPOCH` or the Debian changelog. Builds in an
identical Debian toolchain should also be compared for reproducibility; distinct
distribution/tool versions are not promised to produce identical `.deb` bytes.

Workflow permissions default to read-only. Only the tag release job has
`contents: write`; it consumes artifacts from the same successful run and checks
their hashes. Third-party Actions are pinned, with Dependabot configured for
reviewable updates. Do not grant untrusted pull requests secrets or a privileged
self-hosted runner.

References: [dh_dkms](https://manpages.debian.org/testing/dh-dkms/dh_dkms.1.en.html),
[Debian package fields](https://www.debian.org/doc/debian-policy/ch-controlfields.html),
[GitHub Actions security](https://docs.github.com/en/actions/reference/security/secure-use).
