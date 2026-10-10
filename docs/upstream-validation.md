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

## Upload ceiling: Mac to Linux

Upload (macOS to Linux) stays at about 24.5 Gbit/s while download reaches
28–30 Gbit/s on this pairing. These experiments, on 2026-10-10/11 with the
same host and peer, looked for the limit on the Linux side. Per-run data is in
[upstream-iperf3-results.csv](upstream-iperf3-results.csv) under the
`ring-size-*` and `dma-credits-*` phases.

Ruled out on the Linux side, each varied with everything else at default:

| Variable | Result for upload |
| --- | --- |
| RX/TX ring size 128, 256, 512, 1024 | 24.4–24.6 in every case; 64 gave 22.9–23.5 (download fell to 11.5) |
| RX buffer refill batch 1, 4, 17, 64 | unchanged |
| Ring interrupt moderation 0 µs vs 128 µs (Linux 7.2.9) | unchanged (24.3 vs 24.6) |
| Receive CPU | the softirq core was 44% busy; 1 and 4 TCP streams gave the same rate with 0 retransmissions |
| Link | 2 lanes × 20 Gb/s in both directions; no errors, drops or bad checksums |
| XDomain `prtcstns` without the 64K-frames bit | unchanged; macOS kept sending 64 KiB aggregates |

On the Mac, no CPU core exceeded 57% during upload (62% during download), so the
peer is not CPU-bound either. Its Thunderbolt objects expose no data-path
counters or per-path credits.

**Link-level DMA credits.** The Thunderbolt core gives the DMA path from the
peer `dma_credits` (default 14) buffer credits at the Linux ingress port. On
this Intel Raptor Lake-P router the stock parameter cannot exceed 14: the core
takes the minimum with the router-reported `max_dma_credits`, which is 14, so
`modprobe thunderbolt dma_credits=32` still programs 14 (debugfs `port1/path`
confirmed). A core built in the 7.2.9 VM with that minimum removed and the
parameter made writable gave, with the net driver reloaded for each value:

| Credits at the Linux ingress hop | Upload, Gbit/s | Download, Gbit/s |
| ---: | ---: | ---: |
| 7 | 14.46 | 28.7–28.9 |
| 10 | 20.59 | 28.2–28.5 |
| 12 | 24.54 | 27.9–28.7 |
| 14 (default) | 24.56 | 28.0–28.5 |
| 16, 20, 24, 32 | 24.4–24.6 | 25.4–29.3 |
| 64, 100 | link unusable | — |

Below 12 credits upload is credit-bound at about 2.06 Gbit/s per credit, which
corresponds to a credit round trip of about 1 µs. From 12 upward it is flat at
24.55 Gbit/s, so the default already sits above the knee and the ceiling is set
elsewhere. Values of 64 and 100 broke the link: ping round trips rose to about
125 ms, RX errors appeared, and returning to 14 did not help until the core was
unloaded and re-probed. Do not raise `dma_credits` above the router's reported
maximum; on this router the parameter has no useful effect at all.

**Conclusion.** The upload ceiling is not set by the Linux receive path, by its
E2E or link-level credits, or by TCP. It lies in the macOS transmit path or in
the interaction between the Mac and this Intel host interface. Deciding between
those needs a different peer for the Mac (a second Mac, or a bare-metal USB4
Linux or Windows machine). A Windows VM with the controller passed through
cannot serve: Windows attaches its USB4 connection manager only to host routers
that firmware describes through ACPI `_OSC` (hardware ID `PCI\USB4_MS_CM`) or
`ACPI\ACPI0015`, and a passed-through device has neither.

