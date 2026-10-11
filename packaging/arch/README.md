# Arch Linux package

`PKGBUILD` builds the same source-only DKMS package as the Debian packaging:
the driver source under `/usr/src/thunderbolt-net-VERSION/`, the defaults in
`/usr/lib/modprobe.d/thunderbolt-net.conf`, and the documentation. Arch's
`dkms` package builds the module for each installed kernel through its pacman
hooks; the matching `linux-headers` (or the headers of your kernel variant)
must be installed. The module is built on installation, not shipped.

Build from a release:

```sh
cd packaging/arch
makepkg -si
```

`sha256sums` is `SKIP` in the repository copy because the archive's hash is
only known once the release exists; `scripts/make-aur.py` fills in `pkgver`
and the hash from a release's `SHA256SUMS` and writes `.SRCINFO` for
publishing to the AUR. The CI job `arch-kernel` builds and installs this
package from the local source archive against the pinned Arch kernel.

The `tests/kernels/arch.json` pin covers the mainline `linux` package. Other
kernel variants (`linux-lts`, `linux-zen`) are not tested; the DKMS build gate
in `dkms.conf` still applies to them.
