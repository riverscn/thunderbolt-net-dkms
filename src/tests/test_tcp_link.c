// SPDX-License-Identifier: GPL-2.0
/* Disposable QEMU only: a virtual Ethernet pair for real TCP/PMTU sessions.
 * tbpeer0 lives in the sender namespace, tbrx0 is the host's ingress. Mode 0
 * forwards the sender's GSO skb with its metadata, like a NIC that preserves
 * segment boundaries. Mode 1 copies its bytes into a fresh skb with a complete
 * checksum and no segmentation metadata, then runs the production RX helper:
 * the information loss of the Thunderbolt transport. With learn=1 the packets
 * leaving tbrx0 toward the peer feed the production bound table, as the
 * driver's transmit path does. This is a protocol-path model, not an emulator
 * of macOS or Thunderbolt DMA.
 */
#include <linux/module.h>
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/tcp.h>
#include <net/ip.h>
#include <net/ip6_checksum.h>
#include "../rx_fixup.h"

static struct net_device *peer, *ingress;
static unsigned int mode, cap = 1280;
static bool learn;
static unsigned long aggregates, rebuilt, failures, min_mss = 65535;
static unsigned long rebuilt_gso, plain_over_path, max_plain_iplen;
static unsigned long bound_mss, bound_icmp, bound_hits;
module_param(mode, uint, 0444);
module_param(cap, uint, 0444);
module_param(learn, bool, 0444);
module_param(aggregates, ulong, 0444);
module_param(rebuilt, ulong, 0444);
module_param(failures, ulong, 0444);
module_param(min_mss, ulong, 0444);
module_param(rebuilt_gso, ulong, 0444);
module_param(plain_over_path, ulong, 0444);
module_param(max_plain_iplen, ulong, 0444);
module_param(bound_mss, ulong, 0444);
module_param(bound_icmp, ulong, 0444);
module_param(bound_hits, ulong, 0444);

/* Ethernet header length including in-band VLAN tags. */
static unsigned int link_hdr_len(const struct sk_buff *skb)
{
	unsigned int nhoff = ETH_HLEN;
	__be16 proto = ((const struct ethhdr *)skb->data)->h_proto;

	while (eth_type_vlan(proto) && nhoff < ETH_HLEN + 2 * VLAN_HLEN) {
		proto = ((const struct vlan_hdr *)(skb->data + nhoff))->h_vlan_encapsulated_proto;
		nhoff += VLAN_HLEN;
	}
	return nhoff;
}

static struct sk_buff *wire_copy(struct sk_buff *skb)
{
	struct sk_buff *copy;
	struct tcphdr *th;
	unsigned int nhoff = ETH_HLEN, off, len;
	__be16 proto;

	copy = alloc_skb(NET_IP_ALIGN + skb->len, GFP_ATOMIC);
	if (!copy)
		return NULL;
	skb_reserve(copy, NET_IP_ALIGN);
	if (skb_copy_bits(skb, 0, skb_put(copy, skb->len), skb->len))
		goto fail;
	if (copy->len < ETH_HLEN)
		goto fail;
	proto = ((struct ethhdr *)copy->data)->h_proto;
	/* In-band VLAN tags, as a device without VLAN offload emits them. */
	while (eth_type_vlan(proto)) {
		if (nhoff >= ETH_HLEN + 2 * VLAN_HLEN || copy->len < nhoff + VLAN_HLEN)
			goto fail;
		proto = ((struct vlan_hdr *)(copy->data + nhoff))->h_vlan_encapsulated_proto;
		nhoff += VLAN_HLEN;
	}
	if (proto == htons(ETH_P_IP)) {
		struct iphdr *ip = (void *)(copy->data + nhoff);

		if (copy->len < nhoff + sizeof(*ip) || ip->ihl != 5 ||
		    ip->protocol != IPPROTO_TCP)
			goto fail;
		off = nhoff + sizeof(*ip);
		len = copy->len - off;
		if (len < sizeof(*th))
			goto fail;
		ip->tot_len = htons(copy->len - nhoff);
		ip_send_check(ip);
		th = (void *)(copy->data + off);
		th->check = 0;
		th->check = csum_tcpudp_magic(ip->saddr, ip->daddr, len,
					    IPPROTO_TCP, csum_partial(th, len, 0));
	} else if (proto == htons(ETH_P_IPV6)) {
		struct ipv6hdr *ip = (void *)(copy->data + nhoff);

		if (copy->len < nhoff + sizeof(*ip) ||
		    ip->nexthdr != IPPROTO_TCP)
			goto fail;
		off = nhoff + sizeof(*ip);
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

static void record_bounds(void)
{
	struct tbnet_bound_stats stats;

	tbnet_bound_get_stats(ingress, &stats);
	bound_mss = stats.learned_mss;
	bound_icmp = stats.learned_icmp;
	bound_hits = stats.hits;
}

static netdev_tx_t link_xmit(struct sk_buff *skb, struct net_device *dev)
{
	struct net_device *dest = dev == peer ? ingress : peer;

	if (dev == ingress && learn) {
		tbnet_bound_observe_tx(ingress, skb);
		record_bounds();
	}
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
			copy->dev = dest;
			skb = tbnet_rx_fixup(copy, min(READ_ONCE(dest->mtu), cap));
			if (IS_ERR(skb)) {
				failures++;
				return NETDEV_TX_OK;
			}
			rebuilt++;
			if (learn)
				record_bounds();
			if (skb_is_gso(skb)) {
				rebuilt_gso++;
			} else {
				unsigned long iplen = skb->len - link_hdr_len(skb);

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
	/* Let a VLAN device on top keep TSO, so tagged aggregates occur. */
	dev->vlan_features = dev->features;
	eth_hw_addr_random(dev);
}

static int __init link_init(void)
{
	int err;

	if (mode > 1 || cap < 1280 || cap > 9000)
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
	if (learn) {
		err = tbnet_bound_register(ingress);
		if (err) {
			unregister_netdev(ingress);
			unregister_netdev(peer);
			goto free;
		}
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
	if (learn)
		tbnet_bound_unregister(ingress);
	unregister_netdev(peer);
	unregister_netdev(ingress);
	free_netdev(peer);
	free_netdev(ingress);
}
module_init(link_init);
module_exit(link_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Isolated real-TCP metadata-loss test link");
