// SPDX-License-Identifier: GPL-2.0-only
/* Intentionally buggy sanitizer positive control, disposable guest only. */
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/kthread.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
static bool dma_negative;
module_param(dma_negative,bool,0400);
static int dma_sanity(void)
{
 struct device *d;
 struct page *p;
 dma_addr_t a;
 static u64 mask=DMA_BIT_MASK(64);
 d=root_device_register("tbnet-intentional-dma");
 if(IS_ERR(d))return PTR_ERR(d);
 d->dma_mask=&mask;d->coherent_dma_mask=mask;
 p=alloc_page(GFP_KERNEL);
 if(!p){root_device_unregister(d);return -ENOMEM;}
 a=dma_map_page(d,p,0,PAGE_SIZE,DMA_FROM_DEVICE);
 if(dma_mapping_error(d,a)){__free_page(p);root_device_unregister(d);return -EIO;}
 /* Deliberately mismatched size: debug API must diagnose it. Disposable x86 guest. */
 dma_unmap_page(d,a,PAGE_SIZE/2,DMA_FROM_DEVICE);
 __free_page(p);root_device_unregister(d);
 pr_info("RINGLAB SANITY intentional_dma_size_mismatch\n");
 return 0;
}
#if defined(CONFIG_KCSAN)
static int value;
static noinline void step(void) { value++; barrier(); }
static int race(void *p) { while(!kthread_should_stop()){step();cond_resched();}return 0; }
#endif
static int __init init_sanity(void)
{
 if(dma_negative)return dma_sanity();
#if defined(CONFIG_KASAN)
 char *p=kmalloc(32,GFP_KERNEL);
 if(!p)return -ENOMEM;
 kfree(p);pr_info("RINGLAB SANITY intentional_uaf=%d\n",READ_ONCE(p[3]));
#elif defined(CONFIG_KCSAN)
 struct task_struct *a=kthread_create(race,NULL,"intentional-a"),*b=kthread_create(race,NULL,"intentional-b");
 if(IS_ERR(a)||IS_ERR(b))return -ENOMEM;
 kthread_bind(a,0);kthread_bind(b,1);wake_up_process(a);wake_up_process(b);
 msleep(3000);kthread_stop(a);kthread_stop(b);pr_info("RINGLAB SANITY intentional_race=%d\n",value);
#endif
 return 0;
}
static void __exit exit_sanity(void){}
module_init(init_sanity);module_exit(exit_sanity);MODULE_LICENSE("GPL");

MODULE_DESCRIPTION("Isolated intentional sanitizer defects");
