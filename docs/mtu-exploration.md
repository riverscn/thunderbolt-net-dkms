# MTU exploration (unreleased)

Branch `experiment/rx-mtu-auto` starts from the v0.2.0 release commit.
Its automatic mode is not included in the published v0.2.0 package.

## Prototype

`rx_segment_mtu=0` selects the current Thunderbolt netdev MTU for each completed
RX aggregate. Nonzero values retain `min(interface MTU, cap)`. The default and
packaged example remain 1500. The setting itself is read-only after module
load; interface MTU changes are observed without reloading the driver.
A READ_ONCE snapshot is used for each aggregate; an aggregate racing a change
can use the old snapshot. No addresses, bridges or interface MTUs are changed
by the driver.

This controls inferred GSO segment size. It does not recover the sender's MSS
or discover the eventual bridge port, route MTU or remote path MTU.
A 9000-byte ingress MTU is not proof that 9000-byte segments fit an egress.
Do not enable automatic mode on mixed-MTU production forwarding yet.

## First test increment

The diskless kernel tests add 12 cases: IPv4 and IPv6 each follow interface MTU
changes 1500 -> 9000 -> 1280 -> 1500, then check a 1500 cap with a 9000 ingress
and a smaller 1280 ingress. Checks cover software-segmented packet lengths,
sequence numbers, payload and checksums. Module-load checks also exercise zero.
The existing 16 bridge/router cases still use 1500; they do not validate the
mixed-MTU scenarios below. CI results establish the tested revisions.

## Next experiments

Extend the real bridge/router guest harness, with no physical device or host
network changes, to compare IPv4 and IPv6 across these cases:

| Ingress MTU | Egress MTU | Cap | Question |
| ---: | ---: | ---: | --- |
| 1500 | 1500 | 0 | Preserve current forwarding correctness |
| 9000 | 9000 | 0 | Preserve jumbo segmentation and payload |
| 9000 | 1500 | 0 | Detect oversized segments, drops or PMTU responses |
| 9000 | 1500 | 1500 | Conservative-cap control |
| 1500 -> 9000 -> 1500 | matching and smaller | 0 | Observe live MTU changes |

Capture egress segment lengths and bytes, not just transmit success. Separate
bridge behavior from IPv4/IPv6 routing and software GSO from hardware offload
claims. Only after these results should automatic mode be considered as a
default or deployed for hardware performance measurements.

## Source references

- [Netdevice MTU semantics](https://docs.kernel.org/networking/netdevices.html#mtu)
- [GSO segment-size semantics](https://docs.kernel.org/networking/segmentation-offloads.html)
- [v7.0 bridge forwarding](https://github.com/torvalds/linux/blob/v7.0/net/bridge/br_forward.c)
- [v7.0 IPv4 forwarding](https://github.com/torvalds/linux/blob/v7.0/net/ipv4/ip_forward.c)
- [v7.0 IPv6 output](https://github.com/torvalds/linux/blob/v7.0/net/ipv6/ip6_output.c)
