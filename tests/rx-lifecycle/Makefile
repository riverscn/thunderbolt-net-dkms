# SPDX-License-Identifier: GPL-2.0-only
obj-m += tbnet_ring_test.o
tbnet_ring_test-y := harness.o rx_fixup.o
ccflags-y += -I$(src) -DTBNET_VERSION=\"lab\"
KDIR ?= /lib/modules/$(shell uname -r)/build
all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules
obj-m += tbnet_sanity.o
tbnet_sanity-y := sanity.o
