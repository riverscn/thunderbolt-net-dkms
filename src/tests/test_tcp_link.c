// SPDX-License-Identifier: GPL-2.0
/* Disposable QEMU only: a virtual Ethernet pair for real TCP/PMTU tests.
 * Mode 0 preserves normal Linux offload metadata. Mode 1 simulates an RX
 * aggregate with a complete checksum but no original segmentation metadata.
 * This is a protocol-path model, not an emulator of macOS or Thunderbolt DMA.
 */
#include <linux/module.h>
#include <linux/etherdevice.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/tcp.h>
#include <net/ip.h>
#include <net/ip6_checksum.h>
#include "../rx_fixup.h"

static struct net_device *peer, *ingress;
static unsigned int mode, cap;
#ifdef SKB_RX_GSO_MTU_SUPPORTED
static bool core_clamp = true;
#else
static bool core_clamp;
#endif
module_param(core_clamp, bool, 0444);
static unsigned long aggregates, rebuilt, failures, min_mss = 65535;
static unsigned long rebuilt_gso, plain_over_path, max_plain_iplen;
module_param(mode, uint, 0444);
module_param(cap, uint, 0444);
module_param(aggregates, ulong, 0444);
module_param(rebuilt, ulong, 0444);
module_param(failures, ulong, 0444);
module_param(min_mss, ulong, 0444);
module_param(rebuilt_gso, ulong, 0444);
module_param(plain_over_path, ulong, 0444);
module_param(max_plain_iplen, ulong, 0444);

static struct sk_buff *wire_copy(struct sk_buff *skb)
{
	struct sk_buff *copy;
	struct tcphdr *th;
	unsigned int off, len;

	copy = alloc_skb(NET_IP_ALIGN + skb->len, GFP_ATOMIC);
	if (!copy)
		return NULL;
	skb_reserve(copy, NET_IP_ALIGN);
	if (skb_copy_bits(skb, 0, skb_put(copy, skb->len), skb->len))
		goto fail;
	if (skb->protocol == htons(ETH_P_IP)) {
		struct iphdr *ip = (void *)(copy->data + ETH_HLEN);

		if (copy->len < ETH_HLEN + sizeof(*ip) || ip->ihl != 5 ||
		    ip->protocol != IPPROTO_TCP)
			goto fail;
		off = ETH_HLEN + sizeof(*ip);
		len = copy->len - off;
		if (len < sizeof(*th))
			goto fail;
		ip->tot_len = htons(copy->len - ETH_HLEN);
		ip_send_check(ip);
		th = (void *)(copy->data + off);
		th->check = 0;
		th->check = csum_tcpudp_magic(ip->saddr, ip->daddr, len,
					    IPPROTO_TCP, csum_partial(th, len, 0));
	} else if (skb->protocol == htons(ETH_P_IPV6)) {
		struct ipv6hdr *ip = (void *)(copy->data + ETH_HLEN);

		if (copy->len < ETH_HLEN + sizeof(*ip) ||
		    ip->nexthdr != IPPROTO_TCP)
			goto fail;
		off = ETH_HLEN + sizeof(*ip);
		len = copy->len - off;
		if (len < sizeof(*th))
			goto fail;
		ip->payload_len = htons(len);
		th = (void *)(copy->data + off);
		th->check = 0;
		th->check = csum_ipv6_magic(&ip->saddr, &ip->daddr, len,
					  IPPROTO_TCP, csum_partial(th, len, 0));
	} else {
		goto fail;
	}
	copy->ip_summed = CHECKSUM_NONE;
	return copy;
fail:
	kfree_skb(copy);
	return NULL;
}

static netdev_tx_t link_xmit(struct sk_buff *skb, struct net_device *dev)
{
	struct net_device *dest = dev == peer ? ingress : peer;

	if (dev == peer && skb_is_gso(skb)) {
		aggregates++;
		min_mss = min_t(unsigned long, min_mss, skb_shinfo(skb)->gso_size);
		if (mode) {
			struct sk_buff *copy = wire_copy(skb);

			dev_kfree_skb(skb);
			if (!copy) {
				failures++;
				return NETDEV_TX_OK;
			}
			skb = tbnet_rx_fixup(copy, tbnet_rx_effective_mtu(dest, cap));
			if (IS_ERR(skb)) {
				failures++;
				return NETDEV_TX_OK;
			}
#ifdef SKB_RX_GSO_MTU_SUPPORTED
			/* Negative control: same packet and helper, no core opt-in. */
			if (mode == 2)
				skb_shinfo(skb)->flags &= ~SKBFL_RX_GSO_MTU;
#endif
			rebuilt++;
			if (skb_is_gso(skb)) {
				rebuilt_gso++;
			} else {
				unsigned long iplen = skb->len - ETH_HLEN;

				/* The fixture installs a 1280 route. These were GSO on
				 * the sender, but RX normalization left them ordinary.
				 */
				plain_over_path += iplen > 1280;
				max_plain_iplen = max(max_plain_iplen, iplen);
			}
		}
	}
	/* Like a virtual wire, discard route/socket state at the namespace edge. */
	skb_scrub_packet(skb, true);
	skb->dev = dest;
	skb->protocol = eth_type_trans(skb, dest);
	netif_rx(skb);
	return NETDEV_TX_OK;
}

static const struct net_device_ops link_ops = { .ndo_start_xmit = link_xmit };

static void link_setup(struct net_device *dev)
{
	ether_setup(dev);
	dev->netdev_ops = &link_ops;
	dev->min_mtu = 1280;
	dev->max_mtu = 9000;
	dev->features |= NETIF_F_SG | NETIF_F_HW_CSUM | NETIF_F_TSO | NETIF_F_TSO6;
	dev->hw_features = dev->features;
	eth_hw_addr_random(dev);
}

static int __init link_init(void)
{
	int err;

	if (mode > 2 || (cap && (cap < 1280 || cap > 9000)))
		return -EINVAL;
	peer = alloc_netdev(0, "tbpeer0", NET_NAME_UNKNOWN, link_setup);
	ingress = alloc_netdev(0, "tbrx0", NET_NAME_UNKNOWN, link_setup);
	if (!peer || !ingress) {
		err = -ENOMEM;
		goto free;
	}
	err = register_netdev(peer);
	if (err)
		goto free;
	err = register_netdev(ingress);
	if (err) {
		unregister_netdev(peer);
		goto free;
	}
	return 0;
free:
	if (peer)
		free_netdev(peer);
	if (ingress)
		free_netdev(ingress);
	return err;
}

static void __exit link_exit(void)
{
	unregister_netdev(peer);
	unregister_netdev(ingress);
	free_netdev(peer);
	free_netdev(ingress);
}
module_init(link_init);
module_exit(link_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Isolated real-TCP metadata-loss test link");
