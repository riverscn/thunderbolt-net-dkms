# Privacy and publication

Only files in `release-files.txt` enter source archives. The Debian payload uses
an explicit source-file list. CI release artifacts are limited to the audited
source archive, source-only `.deb` and checksums. Working directories, raw logs,
packet captures, SSH configuration, known-host files, keys, machine certificates,
compiled modules, build metadata and private benchmark records are not published.

`scripts/privacy.py` detects common private addresses, personal paths and several
credential/key formats. It is a guard, not a comprehensive secret detector.
Review the entire release allowlist and extracted artifacts before publication.
An additional local deny-list can be supplied through `PRIVATE_SCAN_TERMS_FILE`;
keep that file outside the repository. Matches report categories, not values.

Before filing a report, remove:

- Hostnames, real IP/MAC addresses, machine UUIDs, serial numbers and usernames.
- Personal filesystem paths, SSH material, cookies, tokens and authentication data.
- Full routing tables, packet payloads, share names and application logs unless
  a minimal, reviewed excerpt is essential.

Use generic labels such as `client`, `Linux host`, and `container`, and documentation
addresses such as `192.0.2.10` and `198.51.100.20`. Do not submit an unreviewed
support bundle. The issue form intentionally does not request automatic uploads.

The maintainer name and contact address in project metadata are explicitly public.
Upstream copyright notices, author addresses and source links are retained for
license compliance and attribution. These are not development-environment logs.

Git commits and tags can expose their author's email. Configure the intended
public identity before committing; a GitHub noreply address is also an option.
