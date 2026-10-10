# Upstream baseline validation

This report covers the 0.3.0 candidate (Linux 7.2.9 driver baseline plus the
retained local RX changes) on 2026-10-10. It does not change
the [v0.2.0 report](validation.md). Per-run results are in
[upstream-iperf3-results.csv](upstream-iperf3-results.csv).

## Setup

- Linux host: x86-64 Proxmox `7.0.14-23-pve`, Intel Raptor Lake-P Thunderbolt 4
  controller. These headers do not provide `tb_ring_throttling()`, so the host
  runs the candidate's older-core fallback.
- Linux 7.2: Arch Linux `7.2.9-arch1-1` VM on the same host, with the controller
  passed through by VFIO for the duration of the test. This exercises the
  new-core API path.
- Peer: Apple M3 MacBook Pro, macOS 27.0.1, `net.inet.tcp.tso: 1`, Thunderbolt
  Bridge MTU 1500, 40 Gbit/s link.
- Both drivers loaded with `rx_segment=1 rx_segment_mtu=1500 rx_page_pool=1`
  and default `e2e`. MTU, offloads, bridge membership, IRQ affinity and RPS were
  restored identically after every module switch.
- Single-stream iperf3 from macOS, 10 seconds after one omitted second,
  alternating upload/download. Rates are receiver medians. Every run was checked
  against the Linux netdev byte counters (`transport_verified`).

## Older-core fallback on the host

Phases ran in the order v0.2.0, candidate, candidate, v0.2.0 (six runs each).

| Phase | Upload, Gbit/s | Download, Gbit/s | Retransmissions |
| --- | ---: | ---: | ---: |
| v0.2.0, before and after combined | 24.59 | 29.55 | 0–2 |
| Candidate, two phases combined | 24.60 | 29.33 | 0–2,651 |
| v0.2.0, bridged to a VirtIO VM | 24.57 | 28.35 | 0–32 |
| Candidate, bridged to a VirtIO VM | 24.54 | 27.91 | 0–86 |

Download differed by 0.7% on the host, within the observed spread; upload was
unchanged. Host softirq time per GiB differed by less than 3%. No RX/TX error,
drop, bad-checksum, invalid-packet or softnet-drop increment occurred, and no
kernel warning, Oops or panic was logged across two module switches.

Three runs in the second candidate phase had 1,022–2,651 retransmissions; the
first candidate phase had none. The v0.2.0 report recorded up to 2,189 on the
same setup, so this sample does not establish a regression, but it should be
watched in later runs.

With the candidate loaded:

- 64 MiB random payloads uploaded to the host, downloaded from it, and uploaded
  to the bridged VM all matched their SHA-256 digests.
- Three idle interface down/up cycles regained carrier after 1.27–1.78 seconds
  and returned to bridge forwarding.
- One cycle during a four-stream upload regained carrier after 1.27 seconds;
  throughput returned to 24.6 Gbit/s about four seconds after the interruption
  and the transfer completed.

## New-core API path on Linux 7.2.9

The macOS peer connected directly to the VM (no bridge), using IPv6 link-local
addresses. A kprobe confirmed that each interface open calls
`tb_ring_throttling()` for both rings with 128,000 ns. The 7.2.9 core programs
this when the ring starts as `DIV_ROUND_UP(interval, 256)`, that is 500. The
register itself was not read back because the guest kernel enforces
`CONFIG_IO_STRICT_DEVMEM`.

The control module is the same source built without the throttling call, which
reproduces an external driver that leaves moderation unset on a new core.

| Module | Upload, Gbit/s | Download, Gbit/s | Interrupts/GiB, up / down | Retransmissions |
| --- | ---: | ---: | ---: | ---: |
| Candidate, 2 phases (12 runs) | 24.55 | 28.51 | 5,753 / 5,078 | 0–2 |
| Control, no throttling (6 runs) | 24.26 | 22.73 | 61,286 / 221,052 | 0–5,890 |

Without throttling, interrupts per GiB rose about 11× on upload and 44× on
download, and download throughput fell by about 20%. The candidate keeps
the new core at the old 128-microsecond behavior. 100 pings at 100 ms
intervals averaged 0.90 ms with throttling and 0.78 ms without; a small latency
cost, from one short sample each. No errors, drops or kernel warnings occurred.

CI now asserts this build path: `scripts/check-throttling.sh` compares the
installed module's undefined symbols with the target kernel's headers and
`Module.symvers`, and the pinned 7.2.9 job requires the new API.

## Observations

- **Enumeration after controller handoff.** Moving the controller between host
  and VM dropped the link, and macOS stayed at Type-C CC level without entering
  USB4 until the cable was replugged. The same happened once on first plug-in.
  This occurs before the network driver is involved.
- **Login after reload during property exchange.** In the VM, bringing the
  interface up immediately after loading the module coincided with repeated
  XDomain property-change requests from the peer. Login did not complete until
  one interface down/up. Waiting about 10 seconds before bringing it up worked
  the first time.
- **Delayed page-pool release.** Unloading the candidate logged one
  `stalled pool shutdown ... 1 inflight 60 sec`. It did not repeat, so the page
  was released within the next interval. No leak was established, but the
  delay is recorded.

## Scope

One controller/peer combination and bounded test windows are covered. Physical
disconnect during traffic, suspend/resume, long stress, bridge forwarding on
Linux 7.2 and other controllers were not tested for this candidate. VM results
include virtualization effects and are compared only with each other.

After these runs, RX packet/byte counting moved after RX normalization (dropped
packets are no longer counted as delivered) and a warning was added when ring
throttling cannot be configured. These change statistics and logging only, not
the data path; both builds were compile-checked on Linux 7.0 and 7.2.9 and the
RX lifecycle model reruns in CI, but the hardware runs above predate them.
