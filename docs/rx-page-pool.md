# RX page recycling

Version 0.2.0 adds optional receive-page recycling. `rx_page_pool=0` is the
default; `rx_page_pool=1` retains DMA mappings in a page pool instead of
allocating and mapping a new page for each receive frame. `rx_segment` remains
an independent option for oversized TCP forwarding.

## Lifecycle

The RX ring is primed and DMA paths are enabled before NAPI starts. Pending
completions are explicitly scheduled after activation. Teardown disables new
RX work, waits for NAPI and its post-completion tail, stops the ring, then
releases buffers and the pool. Zero-budget polls do no RX work, and interrupt
rearming requires successful `napi_complete_done()`.

Pooled SKBs retain their page-pool reference until the network stack releases
them; clones may outlive interface teardown. Received bytes are synchronized
for CPU access before packet parsing. The DMA device comes from
`tb_ring_dma_device()`. With 4 KiB pages, order-1 buffers remain 8 KiB, with a
4096-byte receive/synchronization range and space for SKB metadata.

The shared lifecycle and NAPI SKB-allocation changes also apply when recycling
is off. Disabling `rx_page_pool` changes the allocator, not the entire driver;
[install 0.1.1](installation.md#return-to-the-011-baseline) for a full rollback.

## Upstream fixes

This version includes three attributed Linux fixes:

- [68bf02b6b4ad](https://github.com/torvalds/linux/commit/68bf02b6b4ad3f748c6db71fd77b6c0402d252f4):
  disable DMA paths before stopping rings (Fan XinRan).
- [2f1463554d05](https://github.com/torvalds/linux/commit/2f1463554d0561a2fead81e3888604e5c1125e29):
  release the allocated Rx HopID if it differs from the requested ID (Fan Ye).
- [3c8b26ebf525](https://github.com/torvalds/linux/commit/3c8b26ebf525ba5960510f48c6e9936a79ebe76f):
  clear the local login flag after connection setup fails (Fan Ye).

The kernel's Thunderbolt core/NHI implementation is not replaced by this DKMS
module. Source attribution and hashes are in `upstream/provenance.json`.

## Enable and validate

Follow [installation prerequisites and activation](installation.md). Install
`thunderbolt-net-page-pool.conf.example` to opt in on the next module load,
then check `/sys/module/thunderbolt_net/parameters/rx_page_pool` for `Y`.
The driver does not change MTU, bridges, DHCP, routes or IRQ affinity.

Use iperf3 as the primary throughput comparison, holding topology, MTU,
offloads, CPU placement, stream count and duration constant. Record both
directions, retransmissions and errors. Recycling reduces allocation and DMA
mapping work; it does not eliminate checksum, GRO, forwarding or application
cost. Results and test scope are in [validation](validation.md).
