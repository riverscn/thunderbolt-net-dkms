/* SPDX-License-Identifier: GPL-2.0 */
#ifndef TBNET_RX_FIXUP_H
#define TBNET_RX_FIXUP_H

#include <linux/skbuff.h>
#include <linux/netdevice.h>

/* Zero selects the current ingress MTU, not the egress/path MTU. */
static inline unsigned int tbnet_rx_effective_mtu(const struct net_device *dev,
					       unsigned int cap)
{
	unsigned int mtu = READ_ONCE(dev->mtu);

	return cap ? min(mtu, cap) : mtu;
}

/* Input/output data points at the Ethernet header. Always consumes input:
 * returns it unchanged or with validated GSO metadata, or ERR_PTR after freeing it.
 */
struct sk_buff *tbnet_rx_fixup(struct sk_buff *skb, unsigned int mtu);

#endif
