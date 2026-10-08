# SPDX-License-Identifier: GPL-2.0-only
KERNELRELEASE ?= $(shell uname -r)
KDIR ?= /lib/modules/$(KERNELRELEASE)/build
MODULE_DIR := $(abspath src)

.PHONY: all modules test-modules clean check dist deb
all: modules
modules:
	$(MAKE) -C "$(KDIR)" M="$(MODULE_DIR)" W=1 modules
test-modules:
	$(MAKE) -C "$(KDIR)" M="$(MODULE_DIR)" W=1 TBNET_BUILD_TESTS=1 modules
clean:
	@if test -d "$(KDIR)"; then $(MAKE) -C "$(KDIR)" M="$(MODULE_DIR)" clean; fi
check:
	python3 scripts/check-source.py
	python3 -m unittest discover -s tests -v
dist:
	python3 scripts/make-dist.py
deb:
	dpkg-buildpackage --build=binary --no-sign
