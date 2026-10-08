// SPDX-License-Identifier: GPL-2.0
/* Experimental USB4NET RX normalization. Opt-in in the caller.
 * The peer's TCP segment boundaries are not carried by ThunderboltIP.
 * Annotate only a narrowly supported, fully validated TCP packet;
 * never reinterpret unchecked wire data as CHECKSUM_PARTIAL/GSO.
 */
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/tcp.h>
#include <net/gso.h>
#include <net/ip.h>
#include <net/ip6_checksum.h>
#include <net/tcp.h>

#include "rx_fixup.h"

/* Authentication and transport extensions may depend on original segment
 * boundaries. Support only ordinary timestamps/SACK/padding in this PoC.
 */
static bool tbnet_tcp_options_supported(const struct tcphdr *th)
{
	const u8 *p = (const u8 *)(th + 1);
	unsigned int left = th->doff * 4 - sizeof(*th);

	while (left) {
		unsigned int kind = p[0], len;

		if (kind == TCPOPT_EOL)
			return true;
		if (kind == TCPOPT_NOP) {
			p++;
			left--;
			continue;
		}
		if (left < 2 || p[1] < 2 || p[1] > left)
			return false;
		len = p[1];
		if (!((kind == TCPOPT_TIMESTAMP && len == TCPOLEN_TIMESTAMP) ||
		      (kind == TCPOPT_SACK && len >= 10 && (len - 2) % 8 == 0)))
			return false;
		p += len;
		left -= len;
	}
	return true;
}

struct sk_buff *tbnet_rx_fixup(struct sk_buff *skb, unsigned int mtu)
{
	unsigned int nhoff = ETH_HLEN, iphlen, iplen, tcplen, thlen, mss;
	struct tcphdr *th;
	unsigned int gso_type;
	__be16 proto;
	__sum16 check;
	int err = -EINVAL;

	if (mtu < 68 || mtu > 65522 || skb_is_gso(skb) ||
	    skb->ip_summed != CHECKSUM_NONE || skb->encapsulation ||
	    skb_has_frag_list(skb))
		return skb;
	if (!pskb_may_pull(skb, ETH_HLEN))
		goto drop;
	proto = ((struct ethhdr *)skb->data)->h_proto;
	/* At most two in-band VLAN tags; more are outside the PoC scope. */
	while (eth_type_vlan(proto)) {
		const struct vlan_hdr *vh;

		if (nhoff >= ETH_HLEN + 2 * VLAN_HLEN)
			return skb;
		if (!pskb_may_pull(skb, nhoff + VLAN_HLEN))
			goto drop;
		vh = (const void *)(skb->data + nhoff);
		proto = vh->h_vlan_encapsulated_proto;
		nhoff += VLAN_HLEN;
	}
	if (skb->len - nhoff <= mtu)
		return skb;

	if (proto == htons(ETH_P_IP)) {
		const struct iphdr *iph;

		if (!pskb_may_pull(skb, nhoff + sizeof(*iph)))
			goto drop;
		iph = (const void *)(skb->data + nhoff);
		/* IP options, fragments and non-TCP traffic retain stock behavior. */
		if (iph->version != 4 || iph->ihl != 5 ||
		    iph->protocol != IPPROTO_TCP || ip_is_fragment(iph))
			return skb;
		iphlen = sizeof(*iph);
		iplen = ntohs(iph->tot_len);
		if (ip_fast_csum((void *)iph, iph->ihl)) {
			err = -EBADMSG;
			goto drop;
		}
		gso_type = SKB_GSO_TCPV4;
	} else if (proto == htons(ETH_P_IPV6)) {
		const struct ipv6hdr *ip6h;

		if (!pskb_may_pull(skb, nhoff + sizeof(*ip6h)))
			goto drop;
		ip6h = (const void *)(skb->data + nhoff);
		/* No extension headers/fragments/jumbograms in the first version. */
		if (ip6h->version != 6 || ip6h->nexthdr != IPPROTO_TCP ||
		    !ip6h->payload_len)
			return skb;
		iphlen = sizeof(*ip6h);
		iplen = iphlen + ntohs(ip6h->payload_len);
		gso_type = SKB_GSO_TCPV6;
	} else {
		return skb;
	}
	if (iplen < iphlen + sizeof(*th) || iplen > skb->len - nhoff)
		goto drop;
	if (iplen <= mtu)
		return skb;
	if (!pskb_may_pull(skb, nhoff + iphlen + sizeof(*th)))
		goto drop;
	th = (void *)(skb->data + nhoff + iphlen);
	thlen = th->doff * 4;
	tcplen = iplen - iphlen;
	if (thlen < sizeof(*th) || thlen > tcplen)
		goto drop;
	if (!pskb_may_pull(skb, nhoff + iphlen + thlen))
		goto drop;
	th = (void *)(skb->data + nhoff + iphlen);
	/* Reserved/AccECN bits and exceptional control packets need separate
	 * semantic validation; do not invent segment metadata for them.
	 */
	if (!th->ack || th->syn || th->rst || th->urg ||
	    (((const u8 *)th)[12] & 0x0f) ||
	    !tbnet_tcp_options_supported(th) || mtu <= iphlen + thlen)
		return skb;
	mss = mtu - iphlen - thlen;
	if (tcplen - thlen <= mss)
		return skb;

	if (gso_type == SKB_GSO_TCPV4) {
		const struct iphdr *iph = (const void *)(skb->data + nhoff);

		check = csum_tcpudp_magic(iph->saddr, iph->daddr, tcplen,
					 IPPROTO_TCP,
					 skb_checksum(skb, nhoff + iphlen, tcplen, 0));
	} else {
		const struct ipv6hdr *ip6h = (const void *)(skb->data + nhoff);

		check = csum_ipv6_magic(&ip6h->saddr, &ip6h->daddr, tcplen,
				       IPPROTO_TCP,
				       skb_checksum(skb, nhoff + iphlen, tcplen, 0));
	}
	if (check) {
		err = -EBADMSG;
		goto drop;
	}
	/* Clone owners must retain their original shared-info metadata. */
	err = skb_unclone(skb, GFP_ATOMIC);
	if (err)
		goto drop;
	/* Preserve the verified wire checksum; let the egress segment as needed. */
	err = pskb_trim(skb, nhoff + iplen);
	if (err)
		goto drop;
	skb_reset_mac_header(skb);
	skb_set_network_header(skb, nhoff);
	skb_set_transport_header(skb, nhoff + iphlen);
	skb->protocol = ((struct ethhdr *)skb->data)->h_proto;
	skb_shinfo(skb)->gso_size = mss;
	skb_shinfo(skb)->gso_type = gso_type | SKB_GSO_DODGY;
	skb_shinfo(skb)->gso_segs = DIV_ROUND_UP(tcplen - thlen, mss);
	/* Keep the aggregate intact for local delivery and GSO-capable egress.
	 * DODGY forces the generic GSO validation path and avoids merging this
	 * synthetic segmentation boundary into another GRO aggregate.
	 * CHECKSUM_UNNECESSARY is justified only by the full verification above.
	 */
	skb->ip_summed = CHECKSUM_UNNECESSARY;
	skb->csum_valid = 0;
	skb->csum_level = 0;
	return skb;

drop:
	kfree_skb(skb);
	return ERR_PTR(err);
}
