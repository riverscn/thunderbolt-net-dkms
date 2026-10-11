/* SPDX-License-Identifier: GPL-2.0 */
#ifndef TBNET_RX_FIXUP_H
#define TBNET_RX_FIXUP_H

#include <linux/skbuff.h>
#include <linux/netdevice.h>

/* Input/output data points at the Ethernet header. Always consumes input:
 * returns it unchanged or with validated GSO metadata, or ERR_PTR after freeing it.
 * path_mtu is the fallback: with skb->dev set and a bound learned for the
 * flow, that bound is the path MTU instead (never above the device MTU).
 */
struct sk_buff *tbnet_rx_fixup(struct sk_buff *skb, unsigned int path_mtu);

/* Learned per-flow segment bounds. The transport does not carry the sender's
 * MSS, but the control packets that constrain it pass through this host in
 * the transmit direction: the SYN-ACK a remote returns to the peer and the
 * ICMP Fragmentation Needed / Packet Too Big a router sends to it. Both can
 * only lower the segment size compared with the sender's own view, so a
 * forged message costs efficiency, never correctness.
 */
struct tbnet_bound_stats {
	u64 learned_mss;	/* entries set from a SYN-ACK MSS option */
	u64 learned_icmp;	/* entries lowered by ICMP path MTU feedback */
	u64 hits;		/* normalized aggregates that used a learned bound */
};

int tbnet_bound_register(struct net_device *dev);
void tbnet_bound_unregister(struct net_device *dev);
/* Called with a packet about to leave toward the peer (data at the Ethernet header). */
void tbnet_bound_observe_tx(struct net_device *dev, struct sk_buff *skb);
void tbnet_bound_get_stats(struct net_device *dev, struct tbnet_bound_stats *out);

#endif
