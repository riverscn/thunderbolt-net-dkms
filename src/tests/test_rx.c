// SPDX-License-Identifier: GPL-2.0
/* Runs only inside the disposable QEMU guest; never load on a production host. */
#include <linux/module.h>
#include <linux/etherdevice.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/tcp.h>
#include <linux/if_vlan.h>
#include <linux/slab.h>
#include <net/gso.h>
#include "rx_fixup.h"

static struct net_device *testdev;
static struct sk_buff *kept_original;
static unsigned int tests, failures;

/* Independent byte-oriented Internet checksum, not the implementation's
 * skb_checksum()/pseudo-header helpers. All fixture checksums use this.
 */
static u32 sum_bytes(const u8 *p, unsigned int n, u32 sum)
{
	while (n > 1) {
		sum += ((u16)p[0] << 8) | p[1];
		p += 2;
		n -= 2;
	}
	if (n)
		sum += (u16)p[0] << 8;
	return sum;
}

static u16 finish_sum(u32 s)
{
	while (s >> 16)
		s = (s & 0xffff) + (s >> 16);
	return ~s;
}

static u16 tcp_sum(const u8 *ip, const u8 *tcp, unsigned int len, bool v6)
{
	u32 s = sum_bytes(ip + (v6 ? 8 : 12), v6 ? 32 : 8, 0);

	s += IPPROTO_TCP + len;
	return finish_sum(sum_bytes(tcp, len, s));
}

static u8 payload_byte(unsigned int i)
{
	return (i * 37 + 11) % 251;
}

struct fixture {
	bool v6, nonlinear, cloned, timestamp;
	u8 tags;
	unsigned int payload, mtu;
	bool select_mtu;
	unsigned int mtu_cap;
	int mutation;
};

/* mutation: 1 TCP checksum, 2 IP checksum, 3 TCP offset, 4 truncated IP
 * length, 5 fragmented IPv4, 6 UDP, 7 MD5 option, 8 IPv6 extension,
 * 9 SYN, 10 URG, 11 reserved/AE, 12 TCP option malformed.
 */
static struct sk_buff *make_packet(const struct fixture *f)
{
	unsigned int l2 = ETH_HLEN + f->tags * VLAN_HLEN;
	unsigned int ihl = f->v6 ? 40 : 20;
	unsigned int thl = f->timestamp ? 32 : 20;
	unsigned int plen = ihl + thl + f->payload;
	unsigned int len = l2 + plen, i, off, linear;
	u8 *data = kzalloc(len, GFP_KERNEL), *ip, *tcp;
	struct sk_buff *skb, *clone;
	struct ethhdr *eth;
	struct tcphdr *th;

	if (!data)
		return NULL;
	eth = (void *)data;
	eth->h_dest[0] = 2;
	eth->h_source[0] = 4;
	eth->h_proto = htons(f->tags ? ETH_P_8021Q :
					(f->v6 ? ETH_P_IPV6 : ETH_P_IP));
	for (i = 0; i < f->tags; i++) {
		struct vlan_hdr *vh = (void *)(data + ETH_HLEN + i * 4);

		vh->h_vlan_TCI = htons(10 + i);
		vh->h_vlan_encapsulated_proto = htons(i + 1 < f->tags ?
			ETH_P_8021Q : (f->v6 ? ETH_P_IPV6 : ETH_P_IP));
	}
	ip = data + l2;
	tcp = ip + ihl;
	if (f->v6) {
		struct ipv6hdr *h = (void *)ip;

		h->version = 6;
		h->payload_len = htons(thl + f->payload);
		h->nexthdr = IPPROTO_TCP;
		h->hop_limit = 64;
		h->saddr.s6_addr[0] = h->daddr.s6_addr[0] = 0x20;
		h->saddr.s6_addr[1] = h->daddr.s6_addr[1] = 0x01;
		h->saddr.s6_addr[2] = h->daddr.s6_addr[2] = 0x0d;
		h->saddr.s6_addr[3] = h->daddr.s6_addr[3] = 0xb8;
		h->saddr.s6_addr[5] = 1;
		h->daddr.s6_addr[5] = 2;
		h->saddr.s6_addr[15] = 0x10;
		h->daddr.s6_addr[15] = 0x20;
	} else {
		struct iphdr *h = (void *)ip;

		h->version = 4;
		h->ihl = 5;
		h->tot_len = htons(plen);
		h->id = htons(123);
		h->frag_off = htons(0x4000);
		h->ttl = 64;
		h->protocol = IPPROTO_TCP;
		h->saddr = htonl(0xc000020a);
		h->daddr = htonl(0xc6336414);
	}
	th = (void *)tcp;
	th->source = htons(54321);
	th->dest = htons(5001);
	th->seq = htonl(0xfffff000U);
	th->ack_seq = htonl(0x12345678);
	th->doff = thl / 4;
	th->ack = th->fin = th->psh = th->cwr = th->ece = 1;
	th->window = htons(32000);
	if (f->timestamp) {
		u8 *o = tcp + 20;

		o[0] = o[1] = 1;
		o[2] = 8;
		o[3] = 10;
		o[7] = 13;
		o[11] = 42;
	}
	for (i = 0; i < f->payload; i++)
		tcp[thl + i] = payload_byte(i);
	if (f->mutation == 3)
		th->doff = 4;
	if (f->mutation == 4)
		((struct iphdr *)ip)->tot_len = htons(plen + 1);
	if (f->mutation == 5)
		((struct iphdr *)ip)->frag_off = htons(0x2000);
	if (f->mutation == 6)
		((struct iphdr *)ip)->protocol = IPPROTO_UDP;
	if (f->mutation == 7)
		tcp[22] = 19;
	if (f->mutation == 8)
		((struct ipv6hdr *)ip)->nexthdr = 60;
	if (f->mutation == 9)
		th->syn = 1;
	if (f->mutation == 10)
		th->urg = 1;
	if (f->mutation == 11)
		tcp[12] |= 1;
	if (f->mutation == 12)
		tcp[23] = 255;
	th->check = htons(tcp_sum(ip, tcp, thl + f->payload, f->v6));
	if (!f->v6)
		((struct iphdr *)ip)->check = htons(finish_sum(sum_bytes(ip, ihl, 0)));
	if (f->mutation == 1)
		tcp[thl + f->payload - 1] ^= 1;
	if (f->mutation == 2)
		ip[10] ^= 1;
	linear = f->nonlinear ? min(len, 4084U) : len;
	skb = alloc_skb(linear + 64, GFP_KERNEL);
	if (!skb)
		goto out;
	skb_reserve(skb, 32);
	skb_put_data(skb, data, linear);
	for (off = linear; off < len; off += i) {
		struct page *page = alloc_page(GFP_KERNEL);

		if (!page) {
			kfree_skb(skb);
			skb = NULL;
			goto out;
		}
		i = min(len - off, (unsigned int)PAGE_SIZE);
		memcpy(page_address(page), data + off, i);
		skb_add_rx_frag(skb, skb_shinfo(skb)->nr_frags, page, 0, i, PAGE_SIZE);
	}
	skb->dev = testdev;
	if (f->cloned) {
		clone = skb_clone(skb, GFP_KERNEL);
		kept_original = skb;
		skb = clone;
	}
out:
	kfree(data);
	return skb;
}

static bool validate(struct sk_buff *list, const struct fixture *f)
{
	unsigned int l2 = ETH_HLEN + f->tags * 4, ihl = f->v6 ? 40 : 20;
	unsigned int thl = f->timestamp ? 32 : 20, off = 0, count = 0, i;
	struct sk_buff *s;

	for (s = list; s; s = s->next) {
		unsigned int len, payload;
		struct tcphdr *th;
		u8 *b;
		bool ok = true;

		if (s->len < l2 + ihl + thl || s->len - l2 > f->mtu ||
		    skb_is_gso(s) || s->ip_summed == CHECKSUM_PARTIAL)
			return false;
		b = kmalloc(s->len, GFP_KERNEL);
		if (!b)
			return false;
		if (skb_copy_bits(s, 0, b, s->len)) {
			kfree(b);
			return false;
		}
		len = f->v6 ? 40 + ntohs(((struct ipv6hdr *)(b + l2))->payload_len) :
			ntohs(((struct iphdr *)(b + l2))->tot_len);
		payload = s->len - l2 - ihl - thl;
		th = (void *)(b + l2 + ihl);
		if (len != s->len - l2 ||
		    (!f->v6 && finish_sum(sum_bytes(b + l2, ihl, 0))) ||
		    tcp_sum(b + l2, (u8 *)th, len - ihl, f->v6) ||
		    ntohl(th->seq) != 0xfffff000U + off ||
		    ntohl(th->ack_seq) != 0x12345678 ||
		    ntohs(th->source) != 54321 || ntohs(th->dest) != 5001 ||
		    th->doff * 4 != thl || th->fin != !s->next || th->psh != !s->next ||
		    !th->ack || !th->ece || th->cwr != !count)
			ok = false;
		for (i = 0; i < payload; i++)
			if (b[l2 + ihl + thl + i] != payload_byte(off + i))
				ok = false;
		if (f->timestamp && (((u8 *)th)[22] != 8 || ((u8 *)th)[27] != 13 ||
				    ((u8 *)th)[31] != 42))
			ok = false;
		for (i = 0; i < f->tags; i++)
			if (ntohs(((struct vlan_hdr *)(b + 14 + i * 4))->h_vlan_TCI) != 10 + i)
				ok = false;
		kfree(b);
		if (!ok)
			return false;
		off += payload;
		count++;
	}
	return off == f->payload && count == DIV_ROUND_UP(f->payload, f->mtu - ihl - thl);
}

static void __maybe_unused run_case(const char *name, struct fixture f, int expected)
{
	struct sk_buff *skb = make_packet(&f), *segs;
	u32 before = 0;
	bool ok = false;

	tests++;
	if (!skb)
		goto result;
	/* skb_checksum covers payload and headers for bypass identity checking. */
	before = (__force u32)skb_checksum(skb, 0, skb->len, 0);
	local_bh_disable();
	segs = tbnet_rx_fixup(skb, f.select_mtu ?
		tbnet_rx_effective_mtu(testdev, f.mtu_cap) : f.mtu);
	local_bh_enable();
	if (expected < 0) {
		ok = IS_ERR(segs) && PTR_ERR(segs) == expected;
	} else if (expected == 0) {
		ok = segs == skb && !segs->next &&
		     before == (__force u32)skb_checksum(segs, 0, segs->len, 0);
	} else if (!IS_ERR_OR_NULL(segs)) {
		unsigned int ihl = f.v6 ? 40 : 20;
		unsigned int thl = f.timestamp ? 32 : 20;
		struct sk_buff *wire = segs;

		ok = segs == skb && skb_is_gso(segs) &&
		     segs->ip_summed == CHECKSUM_UNNECESSARY &&
		     skb_shinfo(segs)->gso_size == f.mtu - ihl - thl &&
		     skb_shinfo(segs)->gso_segs == DIV_ROUND_UP(f.payload, f.mtu - ihl - thl) &&
		     before == (__force u32)skb_checksum(segs, 0, segs->len, 0);
		if (kept_original)
			ok &= !skb_is_gso(kept_original) &&
			      kept_original->ip_summed == CHECKSUM_NONE;
		local_bh_disable();
		segs = __skb_gso_segment(wire, 0, false);
		local_bh_enable();
		consume_skb(wire);
		ok = ok && !IS_ERR_OR_NULL(segs) && validate(segs, &f);
	}
	if (!IS_ERR_OR_NULL(segs))
		kfree_skb_list(segs);
result:
	kfree_skb(kept_original);
	kept_original = NULL;
	if (!ok)
		failures++;
	pr_info("TBNET_TEST %s %u - %s\n", ok ? "ok" : "not ok", tests, name);
}

#ifndef TBNET_NETWORK_TEST
static int __init test_init(void)
{
	struct fixture f = { .payload = 20001, .mtu = 1500 };
	int n;

	testdev = alloc_etherdev(0);
	if (!testdev)
		return -ENOMEM;
	run_case("IPv4 linear, odd payload, FIN/PSH/CWR, sequence wrap", f, 1);
	f.nonlinear = true;
	run_case("IPv4 paged payload", f, 1);
	f.cloned = true;
	run_case("IPv4 cloned head", f, 1);
	f.timestamp = true;
	run_case("IPv4 timestamps", f, 1);
	f.tags = 1;
	run_case("IPv4 VLAN", f, 1);
	f.tags = 2;
	run_case("IPv4 QinQ", f, 1);
	f.v6 = true;
	run_case("IPv6 QinQ timestamps paged clone", f, 1);
	f.tags = 0;
	f.payload = 64000;
	run_case("IPv6 near-64K packet", f, 1);
	f.v6 = false;
	run_case("IPv4 near-64K packet", f, 1);
	f.mtu = 9000;
	run_case("MTU 9000", f, 1);
	f.mtu = 1280;
	run_case("MTU 1280", f, 1);
	f.payload = 1;
	run_case("small packet unchanged", f, 0);
	f.payload = 1280 - 20 - 32;
	run_case("exact MTU unchanged", f, 0);
	f.payload++;
	run_case("MTU plus one splits", f, 1);
	f.payload = 20001;
	f.mtu = 1500;
	for (n = 1; n <= 12; n++) {
		char name[64];

		f.mutation = n;
		f.v6 = n == 8;
		snprintf(name, sizeof(name), "mutation %d: reject or preserve", n);
		run_case(name, f, n <= 2 ? -EBADMSG : n <= 4 ? -EINVAL : 0);
	}
	f.v6 = true;
	f.mutation = 1;
	run_case("IPv6 bad checksum rejected", f, -EBADMSG);
	/* Change one device's MTU between packets without recreating the device.
	 * Validate resulting wire segment lengths, checksums and payload, not
	 * just the MTU selection arithmetic.
	 */
	for (n = 0; n < 2; n++) {
		static const unsigned int mtus[] = { 1500, 9000, 1280, 1500 };
		unsigned int i;

		f = (struct fixture) { .payload = 20001, .nonlinear = true,
			.v6 = n, .select_mtu = true, .mtu_cap = 0 };
		for (i = 0; i < ARRAY_SIZE(mtus); i++) {
			WRITE_ONCE(testdev->mtu, mtus[i]);
			f.mtu = mtus[i];
			run_case("auto MTU follows successive interface changes", f, 1);
		}
		f.mtu_cap = 1500;
		WRITE_ONCE(testdev->mtu, 9000);
		f.mtu = 1500;
		run_case("explicit cap limits jumbo ingress", f, 1);
		WRITE_ONCE(testdev->mtu, 1280);
		f.mtu = 1280;
		run_case("explicit cap respects smaller ingress", f, 1);
	}
	free_netdev(testdev);
	pr_info("TBNET_TEST SUMMARY tests=%u failures=%u\n", tests, failures);
	return failures ? -EINVAL : 0;
}

static void __exit test_exit(void) {}
module_init(test_init);
module_exit(test_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Isolated VM tests for experimental tbnet RX normalization");
#endif
