// SPDX-License-Identifier: GPL-2.0
/* Hardware-free integration: inject a wire-shaped aggregate into a real
 * Linux bridge or IP forwarding path and capture at a 1500-MTU egress.
 * Never load this test module outside the disposable QEMU guest.
 */
#define TBNET_NETWORK_TEST
#include "test_rx.c"
#include <linux/delay.h>
#include <linux/rtnetlink.h>

static struct net_device *sink;
static DEFINE_SPINLOCK(capture_lock);
static struct sk_buff *captured, **captured_tail;
static bool capturing;
static int trigger;
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

static netdev_tx_t input_xmit(struct sk_buff *skb, struct net_device *dev)
{
	dev_kfree_skb(skb);
	return NETDEV_TX_OK;
}

static const struct net_device_ops input_ops = { .ndo_start_xmit = input_xmit };
static const struct net_device_ops sink_ops = { .ndo_start_xmit = sink_xmit };

static int run_path(const char *value, const struct kernel_param *kp)
{
	struct fixture f = { .payload = 20001, .mtu = 1500, .nonlinear = true };
	struct sk_buff *skb, *next, *list;
	unsigned int packets = 0;
	bool fix, ok, gro;
	int v, err = kstrtoint(value, 0, &v);

	if (err || v < 1 || v > 8 || !testdev)
		return -EINVAL;
	gro = v > 4;
	if (gro)
		v -= 4;
	fix = !(v & 1);
	f.v6 = v > 2;
	skb = make_packet(&f);
	if (!skb)
		return -ENOMEM;
	if (!netif_is_bridge_port(testdev))
		ether_addr_copy(((struct ethhdr *)skb->data)->h_dest, testdev->dev_addr);
	spin_lock_bh(&capture_lock);
	captured = NULL;
	captured_tail = &captured;
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
	spin_unlock_bh(&capture_lock);
	for (skb = list; skb; skb = skb->next)
		packets++;
	ok = fix ? (list && validate(list, &f)) : !list;
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
