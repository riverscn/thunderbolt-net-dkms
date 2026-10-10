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
The existing 16 bridge/router controls still use 1500. The additional 48-case
MTU matrix below exercises the mixed-MTU paths and records unsafe outcomes
separately from valid forwarding. CI results establish the tested revisions.

## Isolated forwarding results — 2026-10-10

[CI run 38017339938](https://github.com/riverscn/thunderbolt-net-dkms/actions/runs/38017339938)
tested commit `114a0f6` on Linux `6.8.0-146-generic`,
`6.12.111+deb13-amd64` and `7.0.0-38-generic`. All three builds, packet tests,
DKMS lifecycle checks and their configured regression tests passed.

Each kernel executed 48 MTU observations: real Linux Bridge and IP forwarding,
IPv4/IPv6, with/without receive GRO. No external NIC or physical Thunderbolt
controller was attached to the diskless guest. Interface MTUs changed through
`ip link set` on the same live devices; the test module was not reloaded.
Each fixture carried 20,001 TCP payload bytes; IPv4 had DF set. The egress test
device advertises no TSO, so output is software-segmented.

All three kernels produced the same outcomes:

| Ingress / egress MTU | Cap | Bridge | IP forwarding |
| --- | ---: | --- | --- |
| 1500 / 1500 | 0 | Valid, maximum IP length 1500 | Same |
| 9000 / 9000 | 0 | Valid, maximum IP length 9000 | Same |
| 9000 / 1500 | 0 | Three frames exceed egress MTU; maximum IP length 9000 | No TCP data at egress |
| 9000 / 1500 | 1500 | Valid, maximum IP length 1500 | Same |
| 1280 / 1280, then 1500 / 1500 | 0 | Follows live MTU changes; valid | Same |

Both IP families and GRO modes agreed. Per kernel, 40 observations delivered
complete payloads with valid sequence numbers/checksums and no oversized
output; four bridge observations produced oversized output; four routed
observations captured no TCP output. Across three kernels this is 144
observations, not 144 successful forwarding cases. See [per-case data](mtu-results.csv).

The software sink intentionally accepts oversized frames for inspection.
Reaching its transmit callback is not proof of successful wire transmission:
a real NIC/peer may reject them. Routing tests did not capture ICMP/PTB replies
or kernel drop reasons, so no particular error response is claimed. All results
are bounded observations of the synthetic traffic, not a complete TCP/PMTUD
session or physical-device performance test.

## Decision and remaining work

Do not make ingress-only automatic mode the default. Keep the 1500 default cap
and preserve explicit nonzero caps. Automatic mode correctly tracks the input
interface, but that information alone does not make mixed-MTU forwarding safe.
The published v0.2.0 behavior is unchanged.

The next design step must distinguish an ingress ceiling from the eventual
forwarding segment limit. Reading only the bridge master MTU cannot establish
all routed/remote path limits either. Before proposing a replacement policy,
extend observations to PMTU error responses and complete TCP sessions, then
cover smaller route MTUs, VLANs and offload-capable egress. Real Thunderbolt
throughput and hotplug tests remain separate and were not run here.

## Source references

- [Netdevice MTU semantics](https://docs.kernel.org/networking/netdevices.html#mtu)
- [GSO segment-size semantics](https://docs.kernel.org/networking/segmentation-offloads.html)
- [v7.0 bridge forwarding](https://github.com/torvalds/linux/blob/v7.0/net/bridge/br_forward.c)
- [v7.0 IPv4 forwarding](https://github.com/torvalds/linux/blob/v7.0/net/ipv4/ip_forward.c)
- [v7.0 IPv6 output](https://github.com/torvalds/linux/blob/v7.0/net/ipv6/ip6_output.c)
