// SPDX-License-Identifier: GPL-2.0
/* Hardware-free integration: inject a wire-shaped aggregate into a real
 * Linux bridge or IP forwarding path and capture at a configurable software egress.
 * Never load this test module outside the disposable QEMU guest.
 */
#define TBNET_NETWORK_TEST
#include "test_rx.c"
#include <linux/delay.h>
#include <linux/rtnetlink.h>
#include <linux/icmp.h>
#include <linux/icmpv6.h>

static struct net_device *sink;
static DEFINE_SPINLOCK(capture_lock);
static struct sk_buff *captured, **captured_tail;
static bool capturing;
static int trigger;
static bool mtu_probe;
static unsigned int mtu_cap = 1500;
static unsigned int route_limit, probe_payload = 20001;
#ifdef SKB_RX_GSO_MTU_SUPPORTED
#define CORE_CLAMP 1
#else
#define CORE_CLAMP 0
#endif
static unsigned int feedback_count, feedback_mtu, feedback_quote_ok;
static unsigned int feedback_stride, feedback_payload;
module_param(mtu_probe, bool, 0600);
module_param(mtu_cap, uint, 0600);
/* route_limit labels the route configured by the guest script; not a policy. */
module_param(route_limit, uint, 0600);
module_param(probe_payload, uint, 0600);
static struct napi_struct test_napi;
static struct sk_buff_head rx_queue;

static int test_poll(struct napi_struct *napi, int budget)
{
	struct sk_buff *skb;
	int done = 0;

	while (done < budget && (skb = skb_dequeue(&rx_queue))) {
		napi_gro_receive(napi, skb);
		done++;
	}
	if (done < budget)
		napi_complete_done(napi, done);
	return done;
}

static netdev_tx_t sink_xmit(struct sk_buff *skb, struct net_device *dev)
{
	struct sk_buff *copy;
	bool tcp = false;

	if (skb->protocol == htons(ETH_P_IP) &&
	    pskb_may_pull(skb, ETH_HLEN + sizeof(struct iphdr)))
		tcp = ((struct iphdr *)(skb->data + ETH_HLEN))->protocol == IPPROTO_TCP;
	else if (skb->protocol == htons(ETH_P_IPV6) &&
		 pskb_may_pull(skb, ETH_HLEN + sizeof(struct ipv6hdr)))
		tcp = ((struct ipv6hdr *)(skb->data + ETH_HLEN))->nexthdr == IPPROTO_TCP;

	spin_lock_bh(&capture_lock);
	if (capturing && tcp) {
		copy = skb_clone(skb, GFP_ATOMIC);
		if (copy) {
			skb_mark_not_on_list(copy);
			*captured_tail = copy;
			captured_tail = &copy->next;
		}
	}
	spin_unlock_bh(&capture_lock);
	dev_kfree_skb(skb);
	return NETDEV_TX_OK;
}

/* Errors can quote the aggregate or any software-generated segment. */
static bool feedback_sequence_ok(__be32 seq)
{
	u32 offset = ntohl(seq) - 0xfffff000U;

	return offset < feedback_payload && feedback_stride &&
	       !(offset % feedback_stride);
}

static netdev_tx_t input_xmit(struct sk_buff *skb, struct net_device *dev)
{
	unsigned int off = skb_network_offset(skb), mtu = 0;
	bool quote_ok = false;
	__be32 ports_seq[2];

	if (skb->protocol == htons(ETH_P_IP)) {
		struct iphdr ip, quoted;
		struct icmphdr icmp;

		if (!skb_copy_bits(skb, off, &ip, sizeof(ip)) && ip.ihl >= 5 &&
		    ip.protocol == IPPROTO_ICMP &&
		    !skb_copy_bits(skb, off + ip.ihl * 4, &icmp, sizeof(icmp)) &&
		    icmp.type == ICMP_DEST_UNREACH && icmp.code == ICMP_FRAG_NEEDED) {
			mtu = ntohs(icmp.un.frag.mtu);
			off += ip.ihl * 4 + sizeof(icmp);
			if (!skb_copy_bits(skb, off, &quoted, sizeof(quoted)) &&
			    quoted.ihl >= 5 && quoted.protocol == IPPROTO_TCP &&
			    !skb_copy_bits(skb, off + quoted.ihl * 4,
					   ports_seq, sizeof(ports_seq)))
				quote_ok = ntohl(ports_seq[0]) == (54321U << 16 | 5001) &&
					   feedback_sequence_ok(ports_seq[1]);
		}
	} else if (skb->protocol == htons(ETH_P_IPV6)) {
		struct ipv6hdr ip, quoted;
		struct icmp6hdr icmp;

		if (!skb_copy_bits(skb, off, &ip, sizeof(ip)) &&
		    ip.nexthdr == IPPROTO_ICMPV6 &&
		    !skb_copy_bits(skb, off + sizeof(ip), &icmp, sizeof(icmp)) &&
		    icmp.icmp6_type == ICMPV6_PKT_TOOBIG && !icmp.icmp6_code) {
			mtu = ntohl(icmp.icmp6_mtu);
			off += sizeof(ip) + sizeof(icmp);
			if (!skb_copy_bits(skb, off, &quoted, sizeof(quoted)) &&
			    quoted.nexthdr == IPPROTO_TCP &&
			    !skb_copy_bits(skb, off + sizeof(quoted),
					   ports_seq, sizeof(ports_seq)))
				quote_ok = ntohl(ports_seq[0]) == (54321U << 16 | 5001) &&
					   feedback_sequence_ok(ports_seq[1]);
		}
	}
	spin_lock_bh(&capture_lock);
	if (capturing && mtu) {
		feedback_count++;
		feedback_mtu = mtu;
		feedback_quote_ok += quote_ok;
	}
	spin_unlock_bh(&capture_lock);
	dev_kfree_skb(skb);
	return NETDEV_TX_OK;
}

static const struct net_device_ops input_ops = { .ndo_start_xmit = input_xmit };
static const struct net_device_ops sink_ops = { .ndo_start_xmit = sink_xmit };

static int run_path(const char *value, const struct kernel_param *kp)
{
	struct fixture f = { .payload = 20001, .mtu = 1500, .nonlinear = true };
	struct sk_buff *skb, *next, *list;
	unsigned int packets = 0, max_ip_len = 0, oversized = 0;
	unsigned int ingress, egress, replies, reported_mtu, quoted_ok, inferred_mtu;
	bool fix, ok, gro;
	int v, err = kstrtoint(value, 0, &v);

	if (err || v < 1 || v > 8 || !testdev || !sink)
		return -EINVAL;
	ingress = READ_ONCE(testdev->mtu);
	egress = READ_ONCE(sink->mtu);
	gro = v > 4;
	if (gro)
		v -= 4;
	fix = !(v & 1);
	f.v6 = v > 2;
	if (mtu_probe) {
		if (!probe_payload || probe_payload > 64000)
			return -EINVAL;
		f.mtu = tbnet_rx_effective_mtu(testdev, mtu_cap);
		f.payload = probe_payload;
	}
	skb = make_packet(&f);
	if (!skb)
		return -ENOMEM;
	if (!netif_is_bridge_port(testdev))
		ether_addr_copy(((struct ethhdr *)skb->data)->h_dest, testdev->dev_addr);
	spin_lock_bh(&capture_lock);
	captured = NULL;
	captured_tail = &captured;
	feedback_count = feedback_mtu = feedback_quote_ok = 0;
	feedback_payload = f.payload;
	feedback_stride = f.mtu - (f.v6 ? 60 : 40);
	capturing = true;
	spin_unlock_bh(&capture_lock);
	local_bh_disable();
	if (fix)
		skb = tbnet_rx_fixup(skb, f.mtu);
	if (!IS_ERR(skb)) {
		while (skb) {
			next = skb->next;
			skb_mark_not_on_list(skb);
			skb->protocol = eth_type_trans(skb, testdev);
			if (gro)
				skb_queue_tail(&rx_queue, skb);
			else
				netif_rx(skb);
			skb = next;
		}
	}
	if (gro)
		napi_schedule(&test_napi);
	local_bh_enable();
	msleep(200);
	spin_lock_bh(&capture_lock);
	capturing = false;
	list = captured;
	captured = NULL;
	replies = feedback_count;
	reported_mtu = feedback_mtu;
	quoted_ok = feedback_quote_ok;
	spin_unlock_bh(&capture_lock);
	inferred_mtu = f.mtu;
	if (mtu_probe && CORE_CLAMP)
		f.mtu = min(f.mtu, min(egress, route_limit ? route_limit : egress));
	for (skb = list; skb; skb = skb->next) {
		unsigned int len = skb->len > ETH_HLEN ? skb->len - ETH_HLEN : 0;

		packets++;
		max_ip_len = max(max_ip_len, len);
		oversized += len > egress;
	}
	ok = fix ? (list && validate(list, &f)) : !list;
	if (mtu_probe) {
		/* The software sink accepts oversized frames so they are visible.
		 * Payload validity does not imply the output fits its declared MTU.
		 */
		pr_info("TBNET_MTU topology=%s ipv=%d gro=%d ingress=%u egress=%u cap=%u effective=%u received=%u max_ip_len=%u oversized=%u valid=%d route_limit=%u payload=%u feedback=%u feedback_mtu=%u quote_ok=%u core_clamp=%u\n",
			netif_is_bridge_port(testdev) ? "bridge" : "route",
			f.v6 ? 6 : 4, gro, ingress, egress, mtu_cap, inferred_mtu,
			packets, max_ip_len, oversized, list && validate(list, &f),
			route_limit, f.payload, replies, reported_mtu, quoted_ok, CORE_CLAMP);
		kfree_skb_list(list);
		trigger = v;
		return 0;
	}
	pr_info("TBNET_PATH %s topology=%s ipv=%d normalize=%d gro=%d received=%u\n",
		ok ? "PASS" : "FAIL", netif_is_bridge_port(testdev) ? "bridge" : "route",
		f.v6 ? 6 : 4, fix, gro, packets);
	kfree_skb_list(list);
	trigger = v;
	return ok ? 0 : -EINVAL;
}

static const struct kernel_param_ops trigger_ops = {
	.set = run_path, .get = param_get_int,
};
module_param_cb(trigger, &trigger_ops, &trigger, 0600);

static int __init path_init(void)
{
	const u8 input_addr[ETH_ALEN] = { 2, 0, 0, 0, 0, 1 };
	const u8 sink_addr[ETH_ALEN] = { 2, 0, 0, 0, 0, 2 };
	int err;

	testdev = alloc_etherdev(0);
	sink = alloc_etherdev(0);
	if (!testdev || !sink) {
		err = -ENOMEM;
		goto free;
	}
	strscpy(testdev->name, "tbtest0", sizeof(testdev->name));
	strscpy(sink->name, "tbsink0", sizeof(sink->name));
	eth_hw_addr_set(testdev, input_addr);
	eth_hw_addr_set(sink, sink_addr);
	testdev->netdev_ops = &input_ops;
	sink->netdev_ops = &sink_ops;
	testdev->features |= NETIF_F_GRO;
	testdev->min_mtu = sink->min_mtu = 68;
	testdev->max_mtu = sink->max_mtu = 9000;
	err = register_netdev(testdev);
	if (err)
		goto free;
	err = register_netdev(sink);
	if (err) {
		unregister_netdev(testdev);
		goto free;
	}
	skb_queue_head_init(&rx_queue);
	netif_napi_add(testdev, &test_napi, test_poll);
	napi_enable(&test_napi);
	return 0;
free:
	if (testdev)
		free_netdev(testdev);
	if (sink)
		free_netdev(sink);
	return err;
}

static void __exit path_exit(void)
{
	napi_disable(&test_napi);
	netif_napi_del(&test_napi);
	skb_queue_purge(&rx_queue);
	unregister_netdev(testdev);
	unregister_netdev(sink);
	free_netdev(testdev);
	free_netdev(sink);
}
module_init(path_init);
module_exit(path_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Disposable-VM bridge/router integration test for tbnet RX fixup");
