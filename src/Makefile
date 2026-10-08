# SPDX-License-Identifier: GPL-2.0-only
obj-m := thunderbolt_net.o
thunderbolt_net-y := main.o trace.o rx_fixup.o
CFLAGS_trace.o := -I$(src)
ccflags-y += -I$(src) -DTBNET_VERSION=\"$(shell cat $(src)/../VERSION)\"

ifeq ($(TBNET_BUILD_TESTS),1)
obj-m += tbnet_rx_test.o tbnet_path_test.o
tbnet_rx_test-y := tests/test_rx.o tests/rx_fixup_under_test.o
tbnet_path_test-y := tests/test_path.o tests/rx_fixup_path.o
endif
