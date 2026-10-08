/* SPDX-License-Identifier: GPL-2.0 */
#ifndef TBNET_RX_FIXUP_H
#define TBNET_RX_FIXUP_H

#include <linux/skbuff.h>

/* Input/output data points at the Ethernet header. Always consumes input:
 * returns it unchanged or with validated GSO metadata, or ERR_PTR after freeing it.
 */
struct sk_buff *tbnet_rx_fixup(struct sk_buff *skb, unsigned int mtu);

#endif
