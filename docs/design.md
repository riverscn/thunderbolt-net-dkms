# Design and limits

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

Segment size is inferred from `min(interface MTU, rx_segment_mtu)` minus headers.
The Thunderbolt transport does not supply the original TCP segment boundaries.
This limitation matters for authentication and transport extensions.

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

The configured cap does not discover route MTUs or downstream tunnels. Use a
cap appropriate for every intended egress path; do not assume this package makes
arbitrary mixed-MTU forwarding safe.

## Parameters and counters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `rx_segment` | false | Enable validated RX GSO metadata |
| `rx_segment_mtu` | 1500 | IP segmentation cap, 68–65522 |
| `e2e` | true | Existing driver's end-to-end flow control option |

Parameters are read-only after loading. `ethtool -S` exposes
`rx_normalized_packets`, `rx_normalized_bytes`, `rx_bad_checksum`, and
`rx_invalid_packets`. Normalized packets count aggregates, not output segments;
normalized bytes include the Ethernet header. Counters reset on device removal.
RX error counters and TCP retransmissions describe different events.

A separate upstream fragment-count bounds fix is included; see [NOTICE](../NOTICE.md).
The module does not configure network addressing, bridging, forwarding, NAT,
IRQ affinity, MTU, macOS settings or controller power management.

References: [Linux segmentation offloads](https://docs.kernel.org/networking/segmentation-offloads.html)
and [Linux v7.0 TCP offload implementation](https://github.com/torvalds/linux/blob/v7.0/net/ipv4/tcp_offload.c).
