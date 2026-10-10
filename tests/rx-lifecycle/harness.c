// SPDX-License-Identifier: GPL-2.0-only
/* Disposable guest: deterministic two-view DMA model and asynchronous ring. */
#include <linux/module.h>
#include <linux/etherdevice.h>
#include <linux/thunderbolt.h>
#include <linux/dma-map-ops.h>
#include <linux/kthread.h>
#include <linux/delay.h>
#include <linux/unaligned.h>
#include <net/page_pool/helpers.h>
static struct device *lab_dma_device(struct tb_ring *r);
static int lab_rx(struct tb_ring *r, struct ring_frame *f);
static struct ring_frame *lab_poll(struct tb_ring *r);
static void lab_stop(struct tb_ring *r);
static void lab_complete(struct tb_ring *r);
static gro_result_t lab_sink(struct napi_struct *n, struct sk_buff *s);
static struct page *lab_alloc(struct page_pool *p);
static struct sk_buff *lab_build(void *p, unsigned int size);
static struct page *lab_raw_alloc(unsigned int order);
#define tb_ring_dma_device lab_dma_device
#define tb_ring_rx lab_rx
#define tb_ring_poll lab_poll
#define tb_ring_stop lab_stop
#define tb_ring_poll_complete lab_complete
#define napi_gro_receive lab_sink
#undef dev_alloc_pages
#define dev_alloc_pages lab_raw_alloc
#define page_pool_dev_alloc_pages lab_alloc
#define napi_build_skb lab_build
#define trace_tbnet_free_frame(...) do {} while (0)
#define trace_tbnet_alloc_rx_frame(...) do {} while (0)
#define trace_tbnet_invalid_rx_ip_frame(...) do {} while (0)
#define trace_tbnet_rx_ip_frame(...) do {} while (0)
#define trace_tbnet_rx_skb(...) do {} while (0)
#include "core.inc"
#undef dev_alloc_pages
#undef page_pool_dev_alloc_pages
#undef napi_build_skb

#define MAPS 2048
#define BASE 0x100000000ULL
struct mapping { void *cpu, *shadow; size_t size; };
static struct mapping table[MAPS];
static DEFINE_SPINLOCK(maplock);
static DEFINE_SPINLOCK(qlock);
static DECLARE_WAIT_QUEUE_HEAD(idle);
static struct device *dma_dev;
static u64 mask = DMA_BIT_MASK(64);
static struct tbnet *net;
static struct tb_ring ring;
static struct task_struct *producer;
static struct sk_buff_head held;
static struct ring_frame *queued[256], *done[256];
static u32 qp,qc,dp,dc,generation;
static bool owned[256];
static atomic_t priming_alloc;
static bool prime_arrivals;
static int startup_race, shutdown_race;
module_param(startup_race,int,0400);
module_param(shutdown_race,int,0400);
static bool running;
static atomic_t inflight, active_maps, nmap, nunmap, failures, received, produced, polls, stop_calls;
static atomic_t alloc_fault=ATOMIC_INIT(-1), map_fault=ATOMIC_INIT(-1), skb_fault=ATOMIC_INIT(-1), submit_fault=ATOMIC_INIT(-1);
static atomic_t limit=ATOMIC_INIT(0), packet_size=ATOMIC_INIT(1500), inject=ATOMIC_INIT(0);
static int result=-EINPROGRESS, negative;
module_param(result,int,0400);
module_param(negative,int,0400); /* 1 skips CPU sync in the model, separate guest. */
static unsigned int cases;
static atomic_t hold_tail, tail_entered, tail_active, tail_stopped;
static atomic_t hold_poll, poll_entered, hold_dma, dma_entered, stop_dma_overlap, stop_poll_overlap;
static void check(bool ok,const char *name) { if (!ok) { atomic_inc(&failures); pr_err("RINGLAB FAIL %s\n",name); } }
static bool fault(atomic_t *v) { return atomic_dec_if_positive(v)==0; }
static struct device *lab_dma_device(struct tb_ring *r) { return dma_dev; }
static struct mapping *find_map(dma_addr_t addr, size_t size, size_t *offset)
{
 unsigned long i;
 if(addr<BASE) return NULL;
 i=(addr-BASE)>>16; *offset=(addr-BASE)&65535;
 if(i>=MAPS || !table[i].cpu || *offset+size>table[i].size) return NULL;
 return &table[i];
}
static dma_addr_t map_phys(struct device *d, phys_addr_t phys,size_t size,enum dma_data_direction dir,unsigned long attrs)
{
 unsigned long flags;unsigned int i;void *shadow;
 if(fault(&map_fault))return DMA_MAPPING_ERROR;
 shadow=kmalloc(size,GFP_ATOMIC);if(!shadow)return DMA_MAPPING_ERROR;
 memcpy(shadow,phys_to_virt(phys),size);
 spin_lock_irqsave(&maplock,flags);
 for(i=0;i<MAPS;i++)if(!table[i].cpu)break;
 if(i==MAPS){spin_unlock_irqrestore(&maplock,flags);kfree(shadow);return DMA_MAPPING_ERROR;}
 table[i]=(struct mapping){.cpu=phys_to_virt(phys),.shadow=shadow,.size=size};
 atomic_inc(&active_maps);atomic_inc(&nmap);
 spin_unlock_irqrestore(&maplock,flags);
 return BASE+((u64)i<<16);
}
static void unmap_phys(struct device *d,dma_addr_t addr,size_t size,enum dma_data_direction dir,unsigned long attrs)
{
 unsigned long flags;size_t off;struct mapping *m;
 spin_lock_irqsave(&maplock,flags);m=find_map(addr,size,&off);
 if(!m || off || m->size!=size){check(false,"unmap_pair");goto out;}
 if(!(attrs&DMA_ATTR_SKIP_CPU_SYNC))memcpy(m->cpu,m->shadow,size);
 kfree(m->shadow);memset(m,0,sizeof(*m));atomic_dec(&active_maps);atomic_inc(&nunmap);
 out:spin_unlock_irqrestore(&maplock,flags);
}
static void sync_cpu(struct device *d,dma_addr_t addr,size_t size,enum dma_data_direction dir)
{
 unsigned long flags;size_t off,start,end;struct mapping *m;
 if(negative)return;
 spin_lock_irqsave(&maplock,flags);m=find_map(addr,size,&off);
 if(!m){check(false,"sync_live_mapping");goto out;}
 start=round_down(off,64);end=min(m->size,round_up(off+size,64));
 memcpy(m->cpu+start,m->shadow+start,end-start);
 out:spin_unlock_irqrestore(&maplock,flags);
}
static void sync_device(struct device *d,dma_addr_t addr,size_t size,enum dma_data_direction dir)
{
 unsigned long flags;size_t off;struct mapping *m;
 spin_lock_irqsave(&maplock,flags);m=find_map(addr,size,&off);
 check(m!=NULL,"device_sync_live_mapping");
 if(m && dir!=DMA_FROM_DEVICE)memcpy(m->shadow+off,m->cpu+off,size);
 spin_unlock_irqrestore(&maplock,flags);
}
static int supported(struct device *d,u64 m) { return m==DMA_BIT_MASK(64); }
static const struct dma_map_ops ops={.map_phys=map_phys,.unmap_phys=unmap_phys,.sync_single_for_cpu=sync_cpu,.sync_single_for_device=sync_device,.dma_supported=supported};
static void prime_pause(void)
{
 /* Allow exactly one completion while the second slot is being allocated.
  * With an early poll, its refill now owns that same second slot. Delaying
  * a process-context allocation exercises the actual driver producer race.
  */
 if(prime_arrivals && !in_softirq() && atomic_inc_return(&priming_alloc)==2)
  msleep(100);
}
static struct page *lab_alloc(struct page_pool *p)
{
 prime_pause();
 return fault(&alloc_fault)?NULL:page_pool_dev_alloc_pages(p);
}
static struct page *lab_raw_alloc(unsigned int order)
{
 prime_pause();
 return __dev_alloc_pages(GFP_ATOMIC | __GFP_NOWARN,order);
}
static struct sk_buff *lab_build(void *p,unsigned int size) { return fault(&skb_fault)?NULL:napi_build_skb(p,size); }
static int lab_rx(struct tb_ring *r,struct ring_frame *f)
{
 unsigned long flags;int err=0;unsigned int index=container_of(f,struct tbnet_frame,frame)-net->rx_ring.frames;
 spin_lock_irqsave(&qlock,flags);
 if(!running)err=-ESHUTDOWN;
 else if(fault(&submit_fault))err=-EIO;
 else if(qp-qc>=256)err=-ENOSPC;
 else if(owned[index]) {
  pr_err("RINGLAB DUPLICATE index=%u prod=%u cons=%u softirq=%d\n",index,net->rx_ring.prod,net->rx_ring.cons,in_softirq()!=0);
  check(false,"duplicate_frame_submission");running=false;err=-EIO;
 } else {owned[index]=true;queued[qp++&255]=f;}
 spin_unlock_irqrestore(&qlock,flags);return err;
}
static struct ring_frame *lab_poll(struct tb_ring *r)
{
 unsigned long flags;struct ring_frame *f=NULL;
 spin_lock_irqsave(&qlock,flags);
 if(running && dc!=dp){f=done[dc++&255];owned[container_of(f,struct tbnet_frame,frame)-net->rx_ring.frames]=false;}
 spin_unlock_irqrestore(&qlock,flags);
 if(f && atomic_read(&hold_poll)) {
  unsigned long until=jiffies+HZ;
  atomic_set(&poll_entered,1);
  while(!READ_ONCE(net->rx_stopping) && time_before(jiffies,until))cpu_relax();
  check(READ_ONCE(net->rx_stopping),"poll_stop_gate_released");
 }
 return f;
}
static void lab_complete(struct tb_ring *r)
{
 unsigned long flags;bool pending;
 if(atomic_read(&hold_tail) && atomic_read(&received)) {
  unsigned long until=jiffies+max(1UL,HZ/10);
  atomic_set(&tail_active,1);atomic_set(&tail_entered,1);
  /* napi_complete_done has released ownership, but this poll still uses
   * the ring. A concurrent stopper must wait for this tail to return.
   */
  while(time_before(jiffies,until))cpu_relax();
  atomic_set(&tail_active,0);
 }
 spin_lock_irqsave(&qlock,flags);pending=running && dp!=dc;spin_unlock_irqrestore(&qlock,flags);
 if(pending)tbnet_start_poll(net);
}
static void lab_stop(struct tb_ring *r)
{
 unsigned long flags;
 atomic_inc(&stop_calls);
 check(!atomic_read(&tail_active),"poll_tail_drained_before_ring_stop");
 spin_lock_irqsave(&qlock,flags);running=false;spin_unlock_irqrestore(&qlock,flags);
 if(atomic_read(&inflight))atomic_inc(&stop_dma_overlap);
 if(atomic_read(&poll_entered))atomic_inc(&stop_poll_overlap);
 wake_up(&idle);
 check(wait_event_timeout(idle,!atomic_read(&inflight),3*HZ)!=0,"device_quiesced");
 spin_lock_irqsave(&qlock,flags);qp=qc=dp=dc=0;memset(owned,0,sizeof(owned));spin_unlock_irqrestore(&qlock,flags);
}
static u8 byte(unsigned int seed,unsigned int total,unsigned int off)
{
 if(off<12)return off==0||off==6?2:0;
 if(off==12)return 0x88;
 if(off==13)return 0xb5;
 if(off<18)return seed>>((off-14)*8);
 if(off<22)return total>>((off-18)*8);
 return (u8)(seed*29+off*37+(off>>8));
}
static void verify(struct sk_buff *skb)
{
 u8 head[8],*buf;unsigned int seed,total,i;
 if(skb_copy_bits(skb,0,head,8)){check(false,"short_packet");return;}
 seed=get_unaligned_le32(head);total=get_unaligned_le32(head+4);
 if(total<64 || total>60000 || skb->len!=total-14){check(false,"packet_length");return;}
 buf=kmalloc(skb->len,GFP_ATOMIC);if(!buf){check(false,"verify_alloc");return;}
 check(!skb_copy_bits(skb,0,buf,skb->len),"copy_payload");
 for(i=0;i<skb->len;i++)if(buf[i]!=byte(seed,total,i+14))break;
 check(i==skb->len,"packet_bytes");kfree(buf);
}
static gro_result_t lab_sink(struct napi_struct *n,struct sk_buff *skb)
{
 struct sk_buff *clone;
 verify(skb);atomic_inc(&received);
 if(skb_queue_len(&held)<32){clone=skb_clone(skb,GFP_ATOMIC);if(clone)skb_queue_tail(&held,clone);}
 kfree_skb(skb);return GRO_NORMAL;
}
static int count_poll(struct napi_struct *n,int budget) { atomic_inc(&polls);return tbnet_poll(n,budget); }
static int device_thread(void *unused)
{
 unsigned int offset=0,seed=1,total=1500,lastgen=0,index=0;
 while(!kthread_should_stop()) {
  unsigned long flags;struct ring_frame *f=NULL;u32 gen=0;bool accept;size_t moff;struct mapping *m;unsigned int n,i,count;int bad;
  spin_lock_irqsave(&qlock,flags);
  if(running && qc!=qp && atomic_read(&produced)<atomic_read(&limit)) {
   gen=generation;f=queued[qc++&255];atomic_inc(&inflight);
  }
  spin_unlock_irqrestore(&qlock,flags);
  if(!f){usleep_range(50,150);continue;}
  if(atomic_read(&hold_dma)) {
   atomic_set(&dma_entered,1);
   check(wait_event_timeout(idle,READ_ONCE(net->rx_stopping),HZ)!=0,"dma_stop_gate_released");
  }
  if(gen!=lastgen){offset=0;index=0;total=atomic_read(&packet_size);seed++;lastgen=gen;}
  count=DIV_ROUND_UP(total,4084);n=min(total-offset,4084U);bad=atomic_read(&inject);
  if((atomic_read(&produced)&15)==0)usleep_range(200,600);
  spin_lock_irqsave(&maplock,flags);m=find_map(f->buffer_phy,4096,&moff);
  if(!m)check(false,"device_write_after_unmap");
  else {
   memset(m->shadow,0,4096);put_unaligned_le32(n,m->shadow);
   put_unaligned_le16(index+(bad==2),m->shadow+4);put_unaligned_le16(seed,m->shadow+6);put_unaligned_le32(count,m->shadow+8);
   for(i=0;i<n;i++)((u8*)m->shadow)[12+i]=byte(seed,total,offset+i);
  }
  spin_unlock_irqrestore(&maplock,flags);
  spin_lock_irqsave(&qlock,flags);accept=running && gen==generation;
  if(accept){f->size=n+12==4096?0:n+12;f->flags=bad==1?RING_DESC_CRC_ERROR:0;done[dp++&255]=f;}
  spin_unlock_irqrestore(&qlock,flags);
  if(accept){atomic_inc(&produced);tbnet_start_poll(net);}
  /* A stop waits for the callback as well as its device write. */
  atomic_dec(&inflight);wake_up(&idle);
  offset+=n;index++;if(offset==total){offset=0;index=0;seed++;total=atomic_read(&packet_size);}
  cond_resched();
 }
 return 0;
}
static int start(unsigned int size,unsigned int frames,bool pooled)
{
 unsigned long flags;int err;
 tbnet_rx_page_pool=pooled;
 atomic_set(&packet_size,size);atomic_set(&limit,prime_arrivals?frames:0);atomic_set(&produced,0);atomic_set(&received,0);
 spin_lock_irqsave(&qlock,flags);generation++;running=true;spin_unlock_irqrestore(&qlock,flags);
 err=tbnet_rx_prepare(net);if(err)return err;
 net->rx_ring_active=true;
 err=tbnet_alloc_rx_buffers(net,256);
 if(!err){tbnet_rx_activate(net);atomic_set(&limit,frames);}
 return err;
}
static void stop(const char *name)
{
 struct sk_buff *skb;int before, calls;
 tbnet_rx_quiesce(net);before=atomic_read(&polls);
 calls=atomic_read(&stop_calls);tbnet_rx_quiesce(net);
 check(calls==atomic_read(&stop_calls),"quiesce_is_idempotent");
 tbnet_start_poll(net);msleep(5);check(before==atomic_read(&polls),"late_notification_ignored");
 check(!net->skb && !net->rx_ring.pool && !net->rx_napi_active && !net->rx_ring_active,"stopped_state");
 while((skb=skb_dequeue(&held))){verify(skb);kfree_skb(skb);}
 for(before=0;before<100 && atomic_read(&active_maps);before++)msleep(20);
 check(!atomic_read(&active_maps),"all_dma_mappings_released");
 cases++;pr_info("RINGLAB CASE %s produced=%d received=%d maps_live=%d failures=%d\n",name,atomic_read(&produced),atomic_read(&received),atomic_read(&active_maps),atomic_read(&failures));
}
static void wait_frames(unsigned int count)
{
 check(wait_event_timeout(idle,atomic_read(&produced)>=count && !atomic_read(&inflight),20*HZ)!=0,"device_progress");
 msleep(30);napi_synchronize(&net->napi);
}
static int tail_stopper(void *unused)
{
 unsigned int i;
 for(i=0;i<2000 && !atomic_read(&tail_entered);i++)usleep_range(100,200);
 check(atomic_read(&tail_entered),"poll_tail_window_reached");
 tbnet_rx_quiesce(net);atomic_set(&tail_stopped,1);return 0;
}
static void tail_case(void)
{
 struct task_struct *stopper;unsigned int i;
 atomic_set(&tail_entered,0);atomic_set(&tail_active,0);atomic_set(&tail_stopped,0);
 check(!start(1500,0,true),"tail_start");
 msleep(10); /* Finish the explicit activation kick before arming the gate. */
 atomic_set(&hold_tail,1);
 stopper=kthread_create(tail_stopper,NULL,"tbnet-stopper");
 if(IS_ERR(stopper)){check(false,"stopper_create");atomic_set(&hold_tail,0);stop("poll_completion_tail");return;}
 kthread_bind(stopper,0);wake_up_process(stopper);atomic_set(&limit,1);
 for(i=0;i<2000 && !atomic_read(&tail_stopped);i++)usleep_range(100,200);
 check(atomic_read(&tail_stopped),"tail_stop_completed");kthread_stop(stopper);
 atomic_set(&hold_tail,0);stop("poll_completion_tail");
}
static int run_suite(void)
{
 unsigned int i,j;int err;unsigned int sizes[]={1500,6000,60000};unsigned int failat[]={1,5,64,256};
 if(shutdown_race){tail_case();return 0;}
 if(!negative)for(i=0;i<2;i++) {
  prime_arrivals=true;atomic_set(&priming_alloc,0);
  err=start(1500,1,i);
  check(!err,"early_completion_start");
  if(!err){wait_frames(1);check(atomic_read(&received)==1,"early_frames_delivered");}
  check(atomic_read(&priming_alloc)>=2,"priming_window_exercised");
  prime_arrivals=false;stop("early_completion_during_prime");
 }
 if(startup_race)return 0;
 if(negative){check(!start(1500,16,true),"negative_start");wait_frames(16);check(!atomic_read(&received),"missing_sync_detected");check(net->stats.rx_length_errors || net->stats.rx_missed_errors,"stale_header_rejected");stop("negative_sync");return 0;}
 tail_case();
 check(!start(1500,0,true),"zero_budget_start");msleep(10);
 { int before=atomic_read(&nmap);u16 prod=net->rx_ring.prod,cons=net->rx_ring.cons;
  check(!tbnet_poll(&net->napi,0),"zero_budget_return");
  check(before==atomic_read(&nmap) && prod==net->rx_ring.prod && cons==net->rx_ring.cons,"zero_budget_no_rx_work");
 }
 stop("zero_budget");
 for(i=0;i<2;i++)for(j=0;j<ARRAY_SIZE(sizes);j++) {
  unsigned int frames=DIV_ROUND_UP(sizes[j],4084)*80;
  check(!start(sizes[j],frames,i),"start");wait_frames(frames);
  check(atomic_read(&received)==80,"all_packets_delivered");stop("wrap_payload_clone");
 }
 atomic_set(&hold_poll,1);check(!start(1500,1,true),"poll_overlap_start");
 for(i=0;i<1000 && !atomic_read(&poll_entered);i++)usleep_range(100,200);
 check(atomic_read(&poll_entered),"poll_overlap_reached");stop("stop_inside_poll");
 atomic_set(&hold_poll,0);atomic_set(&poll_entered,0);
 atomic_set(&hold_dma,1);check(!start(1500,1,true),"dma_overlap_start");
 for(i=0;i<1000 && !atomic_read(&dma_entered);i++)usleep_range(100,200);
 check(atomic_read(&dma_entered),"dma_overlap_reached");stop("late_dma_completion");
 atomic_set(&hold_dma,0);atomic_set(&dma_entered,0);
 check(!start(60000,10000,true),"controller_loss_start");
 check(wait_event_timeout(idle,atomic_read(&produced)>0,10*HZ)!=0,"controller_loss_had_traffic");
 usleep_range(200,400);
 lab_stop(&ring);stop("controller_disappeared");
 for(i=0;i<32;i++) {
  check(!start(i&1?60000:1500,10000,true),"reconnect_start");
  check(wait_event_timeout(idle,atomic_read(&produced)>0,10*HZ)!=0,"disconnect_had_traffic");
  usleep_range(500+(i%5)*100,1500+(i%5)*100);
  stop("stop_during_receive");
 }
 check(!start(60000,1,true),"partial_start");wait_frames(1);check(READ_ONCE(net->skb)!=NULL,"partial_skb_exists");stop("partial_packet_disconnect");
 for(i=1;i<=2;i++){
  atomic_set(&inject,i);check(!start(1500,16,true),"bad_start");wait_frames(16);check(!atomic_read(&received),"bad_frame_rejected");stop("bad_frame");atomic_set(&inject,0);
 }
 for(i=0;i<ARRAY_SIZE(failat);i++){
  atomic_set(&limit,0);atomic_set(&map_fault,failat[i]);err=start(1500,0,true);check(err==-ENOMEM,"map_failure_return");atomic_set(&map_fault,-1);stop("map_failure");
  atomic_set(&alloc_fault,failat[i]);err=start(1500,0,true);check(err==-ENOMEM,"alloc_failure_return");atomic_set(&alloc_fault,-1);stop("allocation_failure");
  atomic_set(&submit_fault,failat[i]);err=start(1500,0,true);check(err==-EIO,"submit_failure_return");atomic_set(&submit_fault,-1);stop("submission_failure");
 }
 check(!start(1500,0,true),"refill_alloc_start");
 atomic_set(&alloc_fault,1);atomic_set(&limit,80);wait_frames(80);
 check(atomic_read(&alloc_fault)==0,"refill_alloc_fault_fired");
 check(atomic_read(&received)==80,"refill_alloc_preserves_queued_frames");
 atomic_set(&alloc_fault,-1);stop("active_refill_allocation_failure");
 check(!start(1500,0,true),"refill_submit_start");
 atomic_set(&submit_fault,1);atomic_set(&limit,80);wait_frames(80);
 check(atomic_read(&submit_fault)==0,"refill_submit_fault_fired");
 check(atomic_read(&received)==80,"refill_submit_preserves_queued_frames");
 atomic_set(&submit_fault,-1);stop("active_refill_submission_failure");
 atomic_set(&skb_fault,1);check(!start(1500,16,true),"skb_failure_start");wait_frames(16);atomic_set(&skb_fault,-1);stop("skb_allocation_failure");
 check(atomic_read(&stop_dma_overlap)>0,"dma_overlap_covered");
 check(atomic_read(&stop_poll_overlap)>0,"poll_overlap_covered");
 pr_info("RINGLAB OVERLAP dma=%d poll=%d\n",atomic_read(&stop_dma_overlap),atomic_read(&stop_poll_overlap));
 return 0;
}
static int __init init_lab(void)
{
 int err;
 skb_queue_head_init(&held);
 dma_dev=root_device_register("tbnet-ring-dma-model");if(IS_ERR(dma_dev))return PTR_ERR(dma_dev);
 dma_dev->dma_mask=&mask;dma_dev->coherent_dma_mask=mask;set_dma_ops(dma_dev,&ops);dma_reset_need_sync(dma_dev);
 if(get_dma_ops(dma_dev)!=&ops){err=-EOPNOTSUPP;goto device;}
 net=NULL;
 {struct net_device *dev=alloc_etherdev(sizeof(*net));if(!dev){err=-ENOMEM;goto device;}net=netdev_priv(dev);net->dev=dev;eth_hw_addr_random(dev);}
 net->rx_ring.ring=&ring;net->rx_stopping=true;
 netif_napi_add_weight(net->dev,&net->napi,count_poll,64);
 producer=kthread_create(device_thread,NULL,"tbnet-model");if(IS_ERR(producer)){err=PTR_ERR(producer);goto free;}
 if(num_online_cpus()>1)kthread_bind(producer,1);
 wake_up_process(producer);
 pr_info("RINGLAB ENV kasan=%d kcsan=%d dma_debug=%d cpus=%u\n",IS_ENABLED(CONFIG_KASAN),IS_ENABLED(CONFIG_KCSAN),IS_ENABLED(CONFIG_DMA_API_DEBUG),num_online_cpus());
 run_suite();kthread_stop(producer);
 result=atomic_read(&failures)?-EINVAL:0;
 pr_info("RINGLAB SUMMARY result=%d cases=%u failures=%d maps=%d unmaps=%d live=%d polls=%d negative=%d\n",result,cases,atomic_read(&failures),atomic_read(&nmap),atomic_read(&nunmap),atomic_read(&active_maps),atomic_read(&polls),negative);
 return 0;
 free:netif_napi_del(&net->napi);free_netdev(net->dev);
 device:root_device_unregister(dma_dev);return err;
}
static void __exit exit_lab(void)
{
 netif_napi_del(&net->napi);free_netdev(net->dev);
 /* page_pool's delayed finalizer retains its own device reference. */
 msleep(1200);set_dma_ops(dma_dev,NULL);root_device_unregister(dma_dev);
}
module_init(init_lab);module_exit(exit_lab);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Candidate RX core concurrency and two-view DMA model tests");
