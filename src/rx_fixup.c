// SPDX-License-Identifier: GPL-2.0
/* Experimental USB4NET RX normalization. Opt-in in the caller.
 * The peer's TCP segment boundaries are not carried by ThunderboltIP.
 * Annotate only a narrowly supported, fully validated TCP packet;
 * never reinterpret unchecked wire data as CHECKSUM_PARTIAL/GSO.
 */
#include <linux/etherdevice.h>
#include <linux/hashtable.h>
#include <linux/icmp.h>
#include <linux/icmpv6.h>
#include <linux/if_vlan.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/jhash.h>
#include <linux/tcp.h>
#include <net/gso.h>
#include <net/ip.h>
#include <net/ip6_checksum.h>
#include <net/tcp.h>

#include "rx_fixup.h"

/* Learned bounds: one global table keyed by device and flow. Entries expire
 * like the kernel's own path MTU cache, so a path that widens again recovers
 * after the same ten minutes.
 */
#define TBNET_BOUND_BITS	8
#define TBNET_BOUND_MAX		2048
#define TBNET_BOUND_TTL		(10 * 60 * HZ)

struct tbnet_bound_key {
	struct net_device *dev;
	struct in6_addr src;	/* IPv4 addresses occupy the first four bytes */
	struct in6_addr dst;
	__be16 sport;		/* zero in a destination-only entry */
	__be16 dport;
	u8 family;
	u8 pad[3];
};

struct tbnet_bound_entry {
	struct hlist_node node;
	struct tbnet_bound_key key;
	unsigned int bound;	/* largest IP length the path is known to carry */
	unsigned long stamp;
};

struct tbnet_bound_dev {
	struct list_head node;
	struct net_device *dev;
	struct tbnet_bound_stats stats;
};

static DEFINE_SPINLOCK(tbnet_bound_lock);
static DEFINE_HASHTABLE(tbnet_bound_table, TBNET_BOUND_BITS);
static unsigned int tbnet_bound_count;
static LIST_HEAD(tbnet_bound_devs);

static struct tbnet_bound_dev *tbnet_bound_dev_find(const struct net_device *dev)
{
	struct tbnet_bound_dev *bd;

	list_for_each_entry(bd, &tbnet_bound_devs, node)
		if (bd->dev == dev)
			return bd;
	return NULL;
}

int tbnet_bound_register(struct net_device *dev)
{
	struct tbnet_bound_dev *bd = kzalloc(sizeof(*bd), GFP_KERNEL);

	if (!bd)
		return -ENOMEM;
	bd->dev = dev;
	spin_lock_bh(&tbnet_bound_lock);
	list_add(&bd->node, &tbnet_bound_devs);
	spin_unlock_bh(&tbnet_bound_lock);
	return 0;
}

void tbnet_bound_unregister(struct net_device *dev)
{
	struct tbnet_bound_entry *entry;
	struct tbnet_bound_dev *bd;
	struct hlist_node *tmp;
	unsigned int bucket;

	spin_lock_bh(&tbnet_bound_lock);
	hash_for_each_safe(tbnet_bound_table, bucket, tmp, entry, node) {
		if (entry->key.dev == dev) {
			hash_del(&entry->node);
			tbnet_bound_count--;
			kfree(entry);
		}
	}
	bd = tbnet_bound_dev_find(dev);
	if (bd)
		list_del(&bd->node);
	spin_unlock_bh(&tbnet_bound_lock);
	kfree(bd);
}

void tbnet_bound_get_stats(struct net_device *dev, struct tbnet_bound_stats *out)
{
	struct tbnet_bound_dev *bd;

	memset(out, 0, sizeof(*out));
	spin_lock_bh(&tbnet_bound_lock);
	bd = tbnet_bound_dev_find(dev);
	if (bd)
		*out = bd->stats;
	spin_unlock_bh(&tbnet_bound_lock);
}

static u32 tbnet_bound_hash(const struct tbnet_bound_key *key)
{
	return jhash(key, sizeof(*key), 0);
}

static bool tbnet_bound_stale(const struct tbnet_bound_entry *entry)
{
	return time_after(jiffies, entry->stamp + TBNET_BOUND_TTL);
}

/* Caller holds the lock. Returns a fresh matching entry or NULL. */
static struct tbnet_bound_entry *
tbnet_bound_find(const struct tbnet_bound_key *key, u32 hash)
{
	struct tbnet_bound_entry *entry;
	struct hlist_node *tmp;

	hash_for_each_possible_safe(tbnet_bound_table, entry, tmp, node, hash) {
		if (memcmp(&entry->key, key, sizeof(*key)))
			continue;
		if (tbnet_bound_stale(entry)) {
			hash_del(&entry->node);
			tbnet_bound_count--;
			kfree(entry);
			return NULL;
		}
		return entry;
	}
	return NULL;
}

/* Caller holds the lock. Drops stale entries in the bucket; if the table is
 * still full, the bucket's oldest entry makes room, else nothing is stored.
 */
static bool tbnet_bound_make_room(u32 hash)
{
	struct tbnet_bound_entry *entry, *oldest = NULL;
	struct hlist_node *tmp;

	hash_for_each_possible_safe(tbnet_bound_table, entry, tmp, node, hash) {
		if (tbnet_bound_stale(entry)) {
			hash_del(&entry->node);
			tbnet_bound_count--;
			kfree(entry);
		} else if (!oldest || time_before(entry->stamp, oldest->stamp)) {
			oldest = entry;
		}
	}
	if (tbnet_bound_count < TBNET_BOUND_MAX)
		return true;
	if (!oldest)
		return false;
	hash_del(&oldest->node);
	tbnet_bound_count--;
	kfree(oldest);
	return true;
}

static void tbnet_bound_store(const struct tbnet_bound_key *key,
			      unsigned int bound, bool lower_only, bool icmp)
{
	struct tbnet_bound_entry *entry;
	struct tbnet_bound_dev *bd;
	u32 hash = tbnet_bound_hash(key);

	spin_lock_bh(&tbnet_bound_lock);
	bd = tbnet_bound_dev_find(key->dev);
	if (!bd)
		goto out;
	entry = tbnet_bound_find(key, hash);
	if (entry) {
		if (lower_only && bound >= entry->bound) {
			entry->stamp = jiffies;
			goto out;
		}
	} else {
		if (!tbnet_bound_make_room(hash))
			goto out;
		entry = kzalloc(sizeof(*entry), GFP_ATOMIC);
		if (!entry)
			goto out;
		entry->key = *key;
		hash_add(tbnet_bound_table, &entry->node, hash);
		tbnet_bound_count++;
	}
	entry->bound = bound;
	entry->stamp = jiffies;
	if (icmp)
		bd->stats.learned_icmp++;
	else
		bd->stats.learned_mss++;
out:
	spin_unlock_bh(&tbnet_bound_lock);
}

static void tbnet_bound_key_init(struct tbnet_bound_key *key,
				 struct net_device *dev, bool ipv4,
				 const void *src, const void *dst,
				 __be16 sport, __be16 dport)
{
	memset(key, 0, sizeof(*key));
	key->dev = dev;
	key->family = ipv4 ? 4 : 6;
	memcpy(&key->src, src, ipv4 ? 4 : 16);
	memcpy(&key->dst, dst, ipv4 ? 4 : 16);
	key->sport = sport;
	key->dport = dport;
}

/* Largest IP length the forwarding path toward (dst, dport) from (src, sport)
 * is known to carry, or 0. Addresses and ports are those of the peer's packet.
 */
static unsigned int tbnet_bound_lookup(struct net_device *dev, bool ipv4,
				       const void *src, const void *dst,
				       __be16 sport, __be16 dport)
{
	struct tbnet_bound_entry *entry;
	struct tbnet_bound_key key;
	unsigned int bound = 0;

	spin_lock_bh(&tbnet_bound_lock);
	if (list_empty(&tbnet_bound_devs))
		goto out;
	tbnet_bound_key_init(&key, dev, ipv4, src, dst, sport, dport);
	entry = tbnet_bound_find(&key, tbnet_bound_hash(&key));
	if (entry)
		bound = entry->bound;
	key.sport = 0;
	key.dport = 0;
	entry = tbnet_bound_find(&key, tbnet_bound_hash(&key));
	if (entry && (!bound || entry->bound < bound))
		bound = entry->bound;
	if (bound) {
		struct tbnet_bound_dev *bd = tbnet_bound_dev_find(dev);

		if (bd)
			bd->stats.hits++;
	}
out:
	spin_unlock_bh(&tbnet_bound_lock);
	return bound;
}

/* The MSS option of a SYN/SYN-ACK, or 0. */
static unsigned int tbnet_tcp_mss_option(const struct sk_buff *skb,
					 unsigned int thoff, unsigned int thlen)
{
	u8 buffer[MAX_TCP_OPTION_SPACE];
	const u8 *p;
	unsigned int left;

	if (thlen <= sizeof(struct tcphdr) || thlen > sizeof(struct tcphdr) + sizeof(buffer))
		return 0;
	left = thlen - sizeof(struct tcphdr);
	p = skb_header_pointer(skb, thoff + sizeof(struct tcphdr), left, buffer);
	if (!p)
		return 0;
	while (left) {
		unsigned int kind = p[0], len;

		if (kind == TCPOPT_EOL)
			return 0;
		if (kind == TCPOPT_NOP) {
			p++;
			left--;
			continue;
		}
		if (left < 2 || p[1] < 2 || p[1] > left)
			return 0;
		len = p[1];
		if (kind == TCPOPT_MSS && len == TCPOLEN_MSS)
			return (p[2] << 8) | p[3];
		p += len;
		left -= len;
	}
	return 0;
}

/* A SYN-ACK leaving toward the peer carries the remote's MSS, which bounds
 * the peer's segments: payload plus the base headers (RFC 6691).
 */
static void tbnet_bound_observe_synack(struct net_device *dev,
				       const struct sk_buff *skb, bool ipv4,
				       const void *remote, const void *peer,
				       unsigned int thoff, unsigned int iphlen)
{
	struct tbnet_bound_key key;
	struct tcphdr _th;
	const struct tcphdr *th;
	unsigned int mss;

	th = skb_header_pointer(skb, thoff, sizeof(_th), &_th);
	if (!th || !th->syn || !th->ack)
		return;
	mss = tbnet_tcp_mss_option(skb, thoff, th->doff * 4);
	if (!mss)
		return;
	tbnet_bound_key_init(&key, dev, ipv4, peer, remote, th->dest, th->source);
	tbnet_bound_store(&key, mss + iphlen + sizeof(*th), false, false);
}

/* ICMP feedback leaving toward the peer quotes the peer's own packet: its
 * addresses, ports when TCP, and the next hop's MTU. Record the flow and the
 * destination, so later flows start from the known limit.
 */
static void tbnet_bound_observe_icmp(struct net_device *dev, bool ipv4,
				     const void *src, const void *dst,
				     const __be16 *ports, unsigned int mtu)
{
	struct tbnet_bound_key key;

	if (mtu < (ipv4 ? 68 : IPV6_MIN_MTU) || mtu > 65535)
		return;
	if (ports) {
		tbnet_bound_key_init(&key, dev, ipv4, src, dst, ports[0], ports[1]);
		tbnet_bound_store(&key, mtu, true, true);
	}
	tbnet_bound_key_init(&key, dev, ipv4, src, dst, 0, 0);
	tbnet_bound_store(&key, mtu, true, true);
}

void tbnet_bound_observe_tx(struct net_device *dev, struct sk_buff *skb)
{
	unsigned int nhoff = ETH_HLEN;
	struct ethhdr _eth;
	const struct ethhdr *eth;

	if (skb_is_gso(skb) || list_empty(&tbnet_bound_devs))
		return;
	eth = skb_header_pointer(skb, 0, sizeof(_eth), &_eth);
	if (!eth)
		return;
	if (eth->h_proto == htons(ETH_P_IP)) {
		struct iphdr _iph;
		const struct iphdr *iph;
		unsigned int iphlen;

		iph = skb_header_pointer(skb, nhoff, sizeof(_iph), &_iph);
		if (!iph || iph->version != 4 || iph->ihl < 5 || ip_is_fragment(iph))
			return;
		iphlen = iph->ihl * 4;
		if (iph->protocol == IPPROTO_TCP) {
			tbnet_bound_observe_synack(dev, skb, true, &iph->saddr,
						   &iph->daddr, nhoff + iphlen,
						   sizeof(*iph));
		} else if (iph->protocol == IPPROTO_ICMP) {
			struct {
				struct icmphdr icmp;
				struct iphdr inner;
				__be16 ports[2];
			} _m;
			const typeof(_m) *m;
			const __be16 *ports = NULL;

			m = skb_header_pointer(skb, nhoff + iphlen, sizeof(_m), &_m);
			if (!m || m->icmp.type != ICMP_DEST_UNREACH ||
			    m->icmp.code != ICMP_FRAG_NEEDED ||
			    m->inner.version != 4 || m->inner.ihl != 5)
				return;
			if (m->inner.protocol == IPPROTO_TCP && !ip_is_fragment(&m->inner))
				ports = m->ports;
			tbnet_bound_observe_icmp(dev, true, &m->inner.saddr,
						 &m->inner.daddr, ports,
						 ntohs(m->icmp.un.frag.mtu));
		}
	} else if (eth->h_proto == htons(ETH_P_IPV6)) {
		struct ipv6hdr _ip6h;
		const struct ipv6hdr *ip6h;

		ip6h = skb_header_pointer(skb, nhoff, sizeof(_ip6h), &_ip6h);
		if (!ip6h || ip6h->version != 6)
			return;
		if (ip6h->nexthdr == IPPROTO_TCP) {
			tbnet_bound_observe_synack(dev, skb, false, &ip6h->saddr,
						   &ip6h->daddr,
						   nhoff + sizeof(*ip6h),
						   sizeof(*ip6h));
		} else if (ip6h->nexthdr == IPPROTO_ICMPV6) {
			struct {
				struct icmp6hdr icmp;
				struct ipv6hdr inner;
				__be16 ports[2];
			} _m;
			const typeof(_m) *m;
			const __be16 *ports = NULL;

			m = skb_header_pointer(skb, nhoff + sizeof(*ip6h), sizeof(_m), &_m);
			if (!m || m->icmp.icmp6_type != ICMPV6_PKT_TOOBIG ||
			    m->inner.version != 6)
				return;
			if (m->inner.nexthdr == IPPROTO_TCP)
				ports = m->ports;
			tbnet_bound_observe_icmp(dev, false, &m->inner.saddr,
						 &m->inner.daddr, ports,
						 ntohl(m->icmp.icmp6_mtu));
		}
	}
}

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
	/* A bound learned from this flow's control packets is more specific
	 * than the configured cap in either direction, within the device MTU.
	 */
	if (skb->dev) {
		const void *ipp = skb->data + nhoff;
		unsigned int bound;

		if (gso_type == SKB_GSO_TCPV4)
			bound = tbnet_bound_lookup(skb->dev, true,
						   &((const struct iphdr *)ipp)->saddr,
						   &((const struct iphdr *)ipp)->daddr,
						   th->source, th->dest);
		else
			bound = tbnet_bound_lookup(skb->dev, false,
						   &((const struct ipv6hdr *)ipp)->saddr,
						   &((const struct ipv6hdr *)ipp)->daddr,
						   th->source, th->dest);
		if (bound) {
			mtu = min(bound, READ_ONCE(skb->dev->mtu));
			if (iplen <= mtu || mtu <= iphlen + thlen)
				return skb;
		}
	}
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
