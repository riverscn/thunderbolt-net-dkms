// SPDX-License-Identifier: GPL-2.0
/* Inject ordered TCP packets through real NAPI/GRO and capture delivery order.
 * Emulate the driver's net_device header settings without Thunderbolt hardware.
 * Never load this test module outside the disposable QEMU guest.
 */
#define TBNET_NETWORK_TEST
#include "test_rx.c"
#include <linux/completion.h>
#include <linux/rtnetlink.h>
#include <net/gro.h>

#define TRANSPORT_HEADER_LEN 12
#define INITIAL_SEQ 0xfffff000U
#define PACKETS_PER_CASE 8

static struct napi_struct order_napi;
static struct sk_buff_head order_queue;
static DECLARE_COMPLETION(polled);
static DEFINE_SPINLOCK(order_lock);
static struct sk_buff *received, **received_tail;
static bool recording;
static unsigned int gro_counts[8], cases, reorder_cases, error_cases;
static int trigger;
static bool legacy_header;
module_param(legacy_header, bool, 0400);
MODULE_PARM_DESC(legacy_header, "Reproduce the old header length as a negative control");

static int order_poll(struct napi_struct *napi, int budget)
{
	struct sk_buff *skb;
	int done = 0;

	while (done < budget && (skb = skb_dequeue(&order_queue))) {
		gro_result_t result = napi_gro_receive(napi, skb);

		if ((unsigned int)result < ARRAY_SIZE(gro_counts))
			gro_counts[result]++;
		done++;
	}
	if (done < budget) {
		napi_complete_done(napi, done);
		complete(&polled);
	}
	return done;
}

static rx_handler_result_t capture_rx(struct sk_buff **pskb)
{
	struct sk_buff *skb = *pskb, *copy;

	spin_lock(&order_lock);
	if (recording) {
		copy = skb_clone(skb, GFP_ATOMIC);
		if (copy) {
			skb_mark_not_on_list(copy);
			*received_tail = copy;
			received_tail = &copy->next;
		}
	}
	spin_unlock(&order_lock);
	consume_skb(skb);
	return RX_HANDLER_CONSUMED;
}

static netdev_tx_t order_xmit(struct sk_buff *skb, struct net_device *dev)
{
	dev_kfree_skb(skb);
	return NETDEV_TX_OK;
}
static const struct net_device_ops order_ops = { .ndo_start_xmit = order_xmit };

static struct sk_buff *sequence_packet(struct fixture *f, unsigned int offset,
				       bool push)
{
	struct sk_buff *skb = make_packet(f);
	unsigned int ihl = f->v6 ? 40 : 20, thl = f->timestamp ? 32 : 20;
	unsigned int i;
	u8 *data, *ip, *tcp;
	struct tcphdr *th;

	if (!skb)
		return NULL;
	data = kmalloc(skb->len, GFP_KERNEL);
	if (!data)
		goto fail;
	if (skb_copy_bits(skb, 0, data, skb->len))
		goto free;
	ether_addr_copy(((struct ethhdr *)data)->h_dest, testdev->dev_addr);
	ip = data + ETH_HLEN;
	tcp = ip + ihl;
	th = (void *)tcp;
	th->seq = htonl(INITIAL_SEQ + offset);
	th->fin = th->cwr = th->ece = 0;
	th->psh = push;
	th->check = 0;
	for (i = 0; i < f->payload; i++)
		tcp[thl + i] = payload_byte(offset + i);
	th->check = htons(tcp_sum(ip, tcp, thl + f->payload, f->v6));
	if (!f->v6) {
		struct iphdr *h = (void *)ip;

		/* Zero IDs keep ordinary same-sized IPv4 packets mergeable. */
		h->id = 0;
		h->check = 0;
		h->check = htons(finish_sum(sum_bytes(ip, ihl, 0)));
	}
	if (skb_store_bits(skb, 0, data, skb->len))
		goto free;
	kfree(data);
	return skb;
free:
	kfree(data);
fail:
	kfree_skb(skb);
	return NULL;
}

static int order_case(unsigned int pattern, bool fix, bool v6,
		      bool nonlinear, bool timestamp, bool push)
{
	struct fixture f = { .mtu = 1500, .v6 = v6,
		.nonlinear = nonlinear, .timestamp = timestamp };
	unsigned int mss = 1500 - (v6 ? 40 : 20) - (timestamp ? 32 : 20);
	unsigned int lengths[][PACKETS_PER_CASE] = {
		{mss, mss, mss, mss, 1, mss, mss, 17},
		{mss, 20001, mss, 20001, mss, 20001, mss, 17},
		{20001, mss, 20001, mss, 20001, mss, 20001, 17},
		{mss, mss * 4, mss, mss * 4, mss, mss * 4, mss, 17},
		{mss * 4, mss, mss * 4, mss, mss * 4, mss, mss * 4, 17},
		{32000, 32000, 32000, 32000, 32000, 32000, 32000, 17},
		{60000, mss, 20001, mss, 60000, mss, 20001, 17},
		{17, 20001, 17, 20001, 17, 20001, 17, 1},
	};
	struct byte_range { unsigned int start, len; } ranges[PACKETS_PER_CASE];
	struct sk_buff_head staging;
	struct sk_buff *skb, *list;
	unsigned int i, bytes = 0, expected = 0, got_bytes = 0, packets = 0;
	bool ok = true;
	bool reordered = false;
	int err = 0;
	skb_queue_head_init(&staging);
	for (i = 0; i < PACKETS_PER_CASE; i++) {
		f.payload = lengths[pattern][i];
		skb = sequence_packet(&f, bytes, push || i == PACKETS_PER_CASE - 1);
		if (!skb) {
			err = -ENOMEM;
			goto purge;
		}
		bytes += f.payload;
		if (fix) {
			skb = tbnet_rx_fixup(skb, f.mtu);
			if (IS_ERR(skb)) {
				err = PTR_ERR(skb);
				goto purge;
			}
		}
		skb->protocol = eth_type_trans(skb, testdev);
		skb_queue_tail(&staging, skb);
	}
	reinit_completion(&polled);
	memset(gro_counts, 0, sizeof(gro_counts));
	spin_lock_bh(&order_lock);
	received = NULL;
	received_tail = &received;
	recording = true;
	spin_unlock_bh(&order_lock);
	local_bh_disable();
	while ((skb = skb_dequeue(&staging)))
		skb_queue_tail(&order_queue, skb);
	napi_schedule(&order_napi);
	local_bh_enable();
	if (!wait_for_completion_timeout(&polled, 5 * HZ)) {
		/* Abort the suite; do not let a late poll contaminate another case. */
		err = -ETIMEDOUT;
		ok = false;
	}
	spin_lock_bh(&order_lock);
	recording = false;
	list = received;
	received = NULL;
	spin_unlock_bh(&order_lock);
	for (skb = list; skb; skb = skb->next) {
		unsigned int ihl = v6 ? 40 : 20, thl = timestamp ? 32 : 20;
		unsigned int len, offset;
		u8 *data = kmalloc(skb->len, GFP_KERNEL);
		struct tcphdr *th;

		if (!data) {
			ok = false;
			break;
		}
		if (skb->len < ihl + thl || skb_copy_bits(skb, 0, data, skb->len)) {
			kfree(data);
			ok = false;
			break;
		}
		th = (void *)(data + ihl);
		offset = ntohl(th->seq) - INITIAL_SEQ;
		len = skb->len - ihl - thl;
		if (packets == ARRAY_SIZE(ranges) || offset > bytes ||
		    !len || len > bytes - offset) {
			kfree(data);
			ok = false;
			break;
		}
		ranges[packets] = (struct byte_range){offset, len};
		if (offset != expected) {
			pr_info("TBNET_ORDER mismatch case=%u packet=%u expected=%u got=%u length=%u\n",
				cases, packets, expected, offset, len);
			reordered = true;
		}
		for (i = 0; i < len; i++)
			if (data[ihl + thl + i] != payload_byte(offset + i)) {
				ok = false;
				pr_info("TBNET_ORDER payload mismatch offset=%u\n", offset + i);
				break;
			}
		expected = offset + len;
		got_bytes += len;
		packets++;
		kfree(data);
	}
	/* Verify byte coverage independently of ordering, so loss, duplication
	 * or corruption cannot satisfy the negative control's reorder assertion.
	 */
	for (i = 1; i < packets; i++) {
		struct byte_range range = ranges[i];
		unsigned int j = i;

		while (j && ranges[j - 1].start > range.start) {
			ranges[j] = ranges[j - 1];
			j--;
		}
		ranges[j] = range;
	}
	expected = 0;
	for (i = 0; i < packets; i++) {
		if (ranges[i].start != expected)
			ok = false;
		expected += ranges[i].len;
	}
	if (got_bytes != bytes || expected != bytes)
		ok = false;
	kfree_skb_list(list);
	pr_info("TBNET_ORDER %s case=%u pattern=%u fix=%u ipv=%u paged=%u ts=%u push=%u packets=%u bytes=%u/%u gro=%u,%u,%u,%u,%u\n",
		!ok ? "ERROR" : (reordered ? "REORDER" : "PASS"),
		cases++, pattern, fix, v6 ? 6 : 4,
		nonlinear, timestamp, push, packets, got_bytes, bytes,
		gro_counts[0], gro_counts[1], gro_counts[2], gro_counts[3], gro_counts[4]);
	if (reordered)
		reorder_cases++;
	if (!ok)
		error_cases++;
purge:
	skb_queue_purge(&staging);
	return err;
}

static int run_order(const char *value, const struct kernel_param *kp)
{
	unsigned int p, flags;
	int err, v;

	if (kstrtoint(value, 0, &v) || v != 1 || !testdev)
		return -EINVAL;
	cases = reorder_cases = error_cases = 0;
	for (p = 0; p < 8; p++)
		for (flags = 0; flags < 32; flags++) {
			err = order_case(p, flags & 1, flags & 2, flags & 4,
					 flags & 8, flags & 16);
			if (err)
				return err;
		}
	pr_info("TBNET_ORDER SUMMARY cases=%u reordered=%u errors=%u header=%u headroom=%u\n",
		cases, reorder_cases, error_cases, testdev->hard_header_len,
		testdev->needed_headroom);
	trigger = v;
	return 0;
}
static const struct kernel_param_ops order_trigger_ops = {
	.set = run_order, .get = param_get_int,
};
module_param_cb(trigger, &order_trigger_ops, &trigger, 0600);

static int __init order_init(void)
{
	const u8 addr[ETH_ALEN] = {2, 0, 0, 0, 0, 1};
	int err;
	testdev = alloc_etherdev(0);
	if (!testdev)
		return -ENOMEM;
	strscpy(testdev->name, "tborder0", sizeof(testdev->name));
	eth_hw_addr_set(testdev, addr);
	testdev->netdev_ops = &order_ops;
	testdev->features |= NETIF_F_GRO;
	/* Match tbnet_probe(): Ethernet headers stay 14 bytes long. The old
	 * setting makes GRO compare 12 bytes of IP header as part of L2.
	 */
	if (legacy_header)
		testdev->hard_header_len += TRANSPORT_HEADER_LEN;
	else
		testdev->needed_headroom = TRANSPORT_HEADER_LEN;
	err = register_netdev(testdev);
	if (err)
		goto free;
	skb_queue_head_init(&order_queue);
	netif_napi_add(testdev, &order_napi, order_poll);
	napi_enable(&order_napi);
	rtnl_lock();
	err = netdev_rx_handler_register(testdev, capture_rx, NULL);
	rtnl_unlock();
	if (!err)
		return 0;
	napi_disable(&order_napi);
	netif_napi_del(&order_napi);
	unregister_netdev(testdev);
free:
	free_netdev(testdev);
	testdev = NULL;
	return err;
}
static void __exit order_exit(void)
{
	napi_disable(&order_napi);
	netif_napi_del(&order_napi);
	rtnl_lock();
	netdev_rx_handler_unregister(testdev);
	rtnl_unlock();
	skb_queue_purge(&order_queue);
	kfree_skb_list(received);
	unregister_netdev(testdev);
	free_netdev(testdev);
}
module_init(order_init);
module_exit(order_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Disposable-VM TCP GRO ordering regression tests");
