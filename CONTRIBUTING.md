# Contributing

Keep changes focused and explain the concrete failure and resulting behavior.
Run `make check` and the Linux container test matrix. Networking changes should
add tests that would fail without the fix, including malformed-input and checksum
handling where relevant. Do not optimize by accepting unchecked wire checksums.

Use kernel C style for driver sources and retain SPDX/copyright notices. Keep
packaging separate from networking behavior changes when possible. Explain any
kernel compatibility guard and test the affected version; do not claim hardware
support from compilation alone.

For driver baseline updates and compatibility work, follow the scope and
validation requirements in [upstream maintenance](docs/upstream-tracking.md) and [the roadmap](docs/roadmap.md).

Review all added files and artifacts for private data. Update `release-files.txt`
with deliberately selected project files only; do not generate it from a dirty
working directory wholesale. Documentation examples use reserved example ranges.

Contributions are under GPL-2.0-only. Include attribution and origin for imported
code. Your Git author identity and pull-request content become public. No
contributor license agreement or private telemetry is required.

For sensitive security findings, follow [SECURITY.md](SECURITY.md).

Preserve upstream changes as individual imports from original commit mail patches,
including author, author date, complete message and `Upstream-commit` SHA. Keep
local compatibility/integration commits separate; do not squash these imports.
See [upstream maintenance](docs/upstream-tracking.md) for preparation and replay.
