# Design and limits

For RX recycling and lifecycle details, see [RX page recycling](rx-page-pool.md).
Its allocator/lifecycle evidence is separate from the baseline hardware results below.

Some peers send a TCP aggregate larger than the receiving interface's MTU.
The stock receive path can deliver such data locally without attaching GSO
metadata. A later forwarding path may treat it as an ordinary oversized packet
and reject it. This project addresses the supported receive representation;
it does not establish that every occurrence is exclusively a Linux or macOS bug.

## Receive normalization

With `rx_segment=1`, the driver checks reconstructed Ethernet packets before
the normal receive/GRO path. For a supported oversized TCP data packet it:

1. Validates lengths, ordinary headers and the supported TCP options.
2. Verifies the complete TCP checksum and, for IPv4, the IP header checksum.
3. Separates shared metadata from cloned owners before changing it.
4. Supplies TCPv4/TCPv6 GSO type, segment size and count with `SKB_GSO_DODGY`.
5. Keeps the verified wire checksum, marks it validated and retains the aggregate.

The local stack receives the aggregate. Generic Linux GSO handles segmentation
where the egress needs it. This avoids splitting every incoming aggregate into
many small packets on the driver receive path. `DODGY` invokes conservative GSO
handling and prevents GRO from merging these synthesized boundaries further.

Segment size is inferred from `min(interface MTU, rx_segment_mtu)` minus headers;
the same value is the threshold above which a packet is treated as an aggregate.
The Thunderbolt transport does not supply the original TCP segment boundaries.
This limitation matters for authentication and transport extensions. The cap
must not exceed the smallest MTU on any path the forwarded flows take; see
[path MTU](path-mtu.md) for the reasoning and a measurement.

## Ethernet header length and GRO ordering

The receive path removes the 12-byte Thunderbolt transport header before handing
the reconstructed Ethernet packet to `eth_type_trans()` and GRO. The net device
must therefore keep the `ETH_HLEN` value set by `alloc_etherdev()`. Transport
space is accounted for separately in `needed_headroom`.

Adding the transport header to `hard_header_len` makes it 26 bytes. In
`gro_list_prepare()`, the non-Ethernet-length comparison then includes the first
12 bytes of the IP header. Length, IPv4 ID or checksum differences can mark two
packets from the same TCP flow as different flows. An earlier packet held by GRO
can consequently be delivered after a later packet that bypasses aggregation.
See the [Linux v7.0 GRO implementation](https://github.com/torvalds/linux/blob/v7.0/net/core/gro.c).

This correction applies with both `rx_segment=0` and `rx_segment=1`; it does not
replace oversized-packet normalization for forwarding. It changes no wire
format, MTU, checksum-validation policy or offload feature flags. It is not a
general fix for all TCP retransmissions or Thunderbolt hotplug delays.

## Scope

Supported: ordinary unfragmented IPv4 TCP without IP options, IPv6 with an
immediate TCP header, up to two in-band VLAN tags, ordinary ACK/data packets,
and timestamps, SACK, NOP/EOL TCP options. Supported malformed packets are dropped
and counted. Unsupported formats retain stock handling; that may retain the
original forwarding problem.

Not normalized: IP fragments/options, IPv6 extension chains/jumbograms, non-TCP,
SYN/RST/URG, reserved/AccECN bits, TCP MD5/AO/MPTCP or unrecognized options,
encapsulated packets and `frag_list` SKBs. Hardware MTU 9000 forwarding, lower-MTU
tunnels, unusual VLAN layouts, and other architectures need separate validation.

The configured cap does not discover route MTUs or downstream tunnels, and no
MTU visible to the driver (ingress interface, bridge or local egress) describes
them. A cap above the real path limit stalls the peer's uploads through this
host; a cap below it only costs efficiency. The packaged default is 1280, the
IPv6 minimum. Raise it only for paths known to carry larger packets; do not
assume this package makes arbitrary mixed-MTU forwarding safe.

## Parameters and counters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `rx_segment` | false | Enable validated RX GSO metadata |
| `rx_segment_mtu` | 1280 | IP segmentation cap, 68–65522; at most the smallest path MTU |
| `rx_segment_learn` | true | Experimental: bound flows from forwarded SYN-ACK MSS and ICMP, see [path MTU](path-mtu.md#learned-bounds--experimental-2026-10-11) |
| `e2e` | true | Existing driver's end-to-end flow control option |

Parameters are read-only after loading. `ethtool -S` exposes
`rx_normalized_packets`, `rx_normalized_bytes`, `rx_bad_checksum`,
`rx_invalid_packets`, `rx_bound_learned_mss`, `rx_bound_learned_icmp` and
`rx_bound_hits`. Normalized packets count aggregates, not output segments;
normalized bytes include the Ethernet header. Counters reset on device removal.
RX error counters and TCP retransmissions describe different events.

A separate upstream fragment-count bounds fix is included; see [NOTICE](../NOTICE.md).
The module does not configure network addressing, bridging, forwarding, NAT,
IRQ affinity, MTU, macOS settings or controller power management.

References: [Linux segmentation offloads](https://docs.kernel.org/networking/segmentation-offloads.html)
and [Linux v7.0 TCP offload implementation](https://github.com/torvalds/linux/blob/v7.0/net/ipv4/tcp_offload.c).
