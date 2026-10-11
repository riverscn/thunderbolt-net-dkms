# Path MTU and the segmentation cap

`rx_segment_mtu` is the one setting of this package that depends on the
network beyond the Thunderbolt link. This page explains what it does, why the
default changed from 1500 to 1280 in 0.3.1, and when to raise it.

## What the cap is

The Thunderbolt transport carries a peer's whole TSO aggregate (up to 64 KiB)
and the receiving driver reassembles it into one packet. The sender's segment
size is not carried, so the Linux host cannot reproduce the original
segmentation. With `rx_segment=1` the driver instead attaches synthetic GSO
metadata so the kernel can split the aggregate before forwarding it.

`rx_segment_mtu` is used twice on the receive path, with one value:

- **Threshold**: a TCP packet whose IP length is at most
  `min(interface MTU, rx_segment_mtu)` is passed through unchanged.
- **Segment size**: a larger one is marked to be split into segments whose IP
  length equals that same value.

It is a guess at the largest IP packet every path beyond the host can carry.
It is not the Thunderbolt interface MTU, it is not negotiated with the peer, and
it does not affect transmission toward the peer.

## Why the guess has to be small

The forwarded flow's sender (for example macOS) runs its own path MTU
discovery. When a hop beyond the Linux host is smaller than the cap, the
sender lowers its MSS, but it keeps aggregating those smaller segments with
TSO. The driver then rebuilds the aggregate with the larger cap, the oversized
segments are dropped at that hop, and the ICMP feedback tells the sender
nothing new. Only its retransmissions, sent as single segments, get through.
The flow does not stop; it crawls.

Reading the Thunderbolt interface MTU, the bridge MTU or the local egress MTU
cannot avoid this: none of them describes a hop two routers away. The branch
`archive/rx-mtu-auto` (kept as a tag) tested an automatic mode that followed
the ingress MTU and found the same stall in isolated TCP sessions. The only
correct value is the sender's current MSS, which the transport does not carry.
Until it does, the cap is an operator setting, and the default has to be the
value least likely to exceed a real path: 1280, the IPv6 minimum MTU.

## Measurement on a PPPoE uplink — 2026-10-11

Topology: macOS 27.0.1 (M3, Thunderbolt Bridge MTU 1500, TSO on) → Thunderbolt
→ Proxmox host `7.0.14-23-pve` with `thunderbolt0` bridged to the LAN → home
router with a **PPPoE WAN, MTU 1492** → Internet → an iperf3 server on a
single-vCPU VPS. This is an ordinary home-network path; nothing was configured
to provoke the failure. The host's default route does not use the Thunderbolt
interface, so reloading the module only interrupted the Mac's connection.

Each phase ran four single-stream uploads from the Mac (`iperf3 -t 10 -O 1`).
The cap was changed by reloading the driver (`rmmod`, then `modprobe` with
explicit `rx_segment=1 rx_segment_mtu=N rx_page_pool=1`), and the first value
was repeated last to bracket the result. The host itself, uploading to the same
server without the Thunderbolt hop, reached 39.3 Mbit/s with 1,817
retransmissions: that is the uplink's own ceiling and loss profile.
Per-run values are in [path-mtu-results.csv](path-mtu-results.csv).

| `rx_segment_mtu` | Upload, Mbit/s (four runs) | Retransmissions | Normalized aggregates per run |
| ---: | --- | ---: | ---: |
| 1500 (0.3.0 default) | 0.1, 0.1, 0.1, 0.1 | 42–79 | 25–47 |
| 1280 | 37.1, 36.2, 2.6, 38.8 | 6–1,184 | 953–14,423 |
| 1492 | 38.5, 30.2, 40.2, 33.4 | 740–1,057 | 5,602–9,912 |
| 1500, repeated | 0.1, 0.1, 0.1, 0.1 | 40–68 | 25–37 |

With the cap at 1500 the Mac's cached path MTU to the server was 1492 and
each upload moved a few dozen aggregates in ten seconds. With 1280 or 1492
the uploads reached the uplink ceiling; the two values are not distinguishable
within the uplink's own variation, and the 2.6 Mbit/s run was a single
outlier whose counters show the link, not the driver, was quiet. The high
retransmission counts at 1280 and 1492 match the host's own direct run.

These are single 10-second uploads on one uplink. They establish the stall and
its removal, not throughput figures for other networks.

## Choosing a value

Set `rx_segment_mtu` to the smallest MTU on any path that traffic from the
Thunderbolt peer will take through this host. Common limits:

| Path | Largest IP packet |
| --- | ---: |
| Plain Ethernet LAN or Internet access without encapsulation | 1500 |
| PPPoE | 1492 |
| WireGuard default | 1420 |
| Many VPN or GRE/IP-in-IP tunnels | 1400 or lower |
| IPv6 minimum, safe for any IPv6 path | 1280 |
| Jumbo LAN with every hop at 9000 | 9000 |

A cap below the real limit costs only efficiency: the host emits more, smaller
packets on the egress interface, and packets between the cap and the
interface MTU that were not aggregates are also verified and re-marked. A cap
above the real limit stalls uploads, as measured above. When unsure, keep 1280.

To change it, copy the package file and edit the copy, then reload or reboot
as described in [installation](installation.md#change-or-disable-the-defaults):

```sh
sudo cp /usr/lib/modprobe.d/thunderbolt-net.conf /etc/modprobe.d/thunderbolt-net.conf
sudoedit /etc/modprobe.d/thunderbolt-net.conf
```

The symptom of a cap that is too large is specific: uploads from the peer
through this host to a particular destination run at a small fraction of the
link, while uploads to the host itself or to the LAN are fast, and
`ethtool -S thunderbolt0` shows no errors. Check the peer's cached path MTU
to that destination (`route -n get ADDRESS` on macOS) against the cap.

## Learned bounds — experimental, 2026-10-11

The sender's MSS is not in the aggregate, but the packets that constrain it
leave this host toward the peer, where the driver's transmit path can read
them:

- The **SYN-ACK** a remote returns carries its MSS option. On the PPPoE
  topology above the router clamps it (`mtu_fix`), so the SYN-ACK arriving at
  the Thunderbolt port already says 1452, the exact path limit, while the
  Mac's own SYN advertised 1460.
- The **ICMP Fragmentation Needed / Packet Too Big** a router sends quotes the
  peer's packet (addresses and ports) and the next-hop MTU. With the cap at
  1500, 59 such messages for one 10-second upload passed through the port.

`rx_segment_learn=1` (default on this branch) records both in a small table
keyed by device, flow and destination, expiring after ten minutes like the
kernel's own path MTU cache. A normalized aggregate whose flow or destination
has a record is split at that bound instead of `rx_segment_mtu`, never above
the interface MTU. Both sources can only make segments smaller than the sender
itself would send, so a forged message costs efficiency, not correctness.
Flows with no record keep the cap, which is why the cap still defaults to 1280.
`ethtool -S` shows `rx_bound_learned_mss`, `rx_bound_learned_icmp` and
`rx_bound_hits`.

Isolated QEMU sessions on Linux 7.0.14 (all interfaces 9000, a 1280 route on
the host, 1 MiB per session, [harness](../scripts/qemu-test.py)):

| Policy | IPv4 retransmissions | IPv6 retransmissions |
| --- | ---: | ---: |
| Original GSO metadata preserved | 28 | 29 |
| Cap 1280, no learning | 15 | 16 |
| Cap 1500, no learning (the stall) | 137 | 293 |
| Cap 1500, learned from ICMP | 27 | 28 |
| Cap 1500, learned from a receiver `advmss` SYN-ACK | 0 | 1 |

In the last row the sender's PMTU stayed at 9000: no packet was ever too big,
because the bound was known before the first aggregate.

On the PPPoE topology, with the module loaded as `rx_segment_mtu=1500
rx_segment_learn=1` (the value that stalled at 0.1 Mbit/s without learning),
four uploads reached 36.9, 6.2, 38.1 and 39.6 Mbit/s against the uplink's
39.3 ceiling; 29,751 of 29,755 normalized aggregates used a learned bound,
98 came from SYN-ACKs and 2 from ICMP. The 6.2 Mbit/s run had zero
retransmissions and a tenth of the aggregates, the same uplink pause seen once
in the cap-1280 phase. Rows `learn-cap1500` in [the CSV](path-mtu-results.csv).

With the shipping defaults (`rx_segment_mtu=1280 rx_segment_learn=1`) and the
fixes below, three 30-second uploads to the same server reached 29.7, 35.7 and
33.4 Mbit/s with no ICMP at all: the clamped SYN-ACK alone set every bound
(rows `learn-default-30s`). On the same load, direct Mac ↔ host iperf3 over the
Thunderbolt link measured 24.6 / 24.6 / 24.6 Gbit/s up and 29.5 / 28.9 /
29.7 Gbit/s down, against 24.6 / 24.6 / 24.6 and 30.0 / 30.0 / 29.8 with the
0.3.0 DKMS module minutes earlier: no measurable cost on the local path, where
every aggregate now also goes through one table lookup. No kernel warning was
logged, and the module unloaded cleanly each time.

`rx_bound_hits` counts lookups that found a record, including packets that
then needed no split, so it can exceed `rx_normalized_packets`.

A learned bound below the cap also lowers the size above which a packet is
examined, so an aggregate between the bound and the cap is split as well;
without that, such packets passed through unmarked and were rejected
downstream (seen as 18 oversized packets in one IPv6 session on Linux 6.12
before the fix). Plain SYNs toward the peer (a remote opening the connection)
and up to two in-band VLAN tags are handled; a VLAN session is part of the
suite.

Limits: a connection established before the driver loaded has no SYN record
and keeps the cap until an ICMP arrives; only ICMP that passes through this
host is seen, which is every ICMP addressed to the peer for flows this host
forwards; the table holds 2048 entries and one global lock, uncontended with a
single receive queue. The bound table is experimental; `rx_segment_learn=0`
restores the cap-only behavior.

## Not yet done

- The cost of 1280 on a jumbo or 10 Gbit/s LAN has not been measured; the
  measurement above was limited by a 40 Mbit/s uplink.
- The threshold and the segment size share one value. Splitting them (threshold
  at the interface MTU, segment size at the cap) would stop ordinary packets
  between the two from being treated as aggregates and would also cover
  aggregates between the cap and the interface MTU, which today pass through
  unmarked. This changes validated receive behavior and needs its own tests.
- Learned bounds have one short hardware run behind them; longer runs, many
  concurrent flows and a second peer type remain to be observed.
