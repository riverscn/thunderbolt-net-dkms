# RX lifecycle regression tests

These modules are **guest-only tests**. Never insert `tbnet_ring_test` or
`tbnet_sanity` into a working host. The latter intentionally causes sanitizer
reports. Neither module is included in the DKMS `.deb`.

`extract.py` copies ten function bodies verbatim from `src/main.c`, verifies
their hashes against `tested-functions.json`, and generates the test build tree.
Changes to those functions require reviewing and updating that evidence before
tests can run. There is no second maintained driver implementation here.

The harness substitutes the NHI ring, DMA operations, and final GRO consumer.
NAPI, SKB, page-pool allocation, DMA API dispatch and reference recycling are
real kernel code. The two-view DMA model uses separate CPU/device buffers and
64-byte sync granularity. It does not emulate hardware cache coherency or NHI
registers. The test EtherType bypasses TCP normalization and the network stack.

## Stock kernel and CI

Use a disposable x86-64 Ubuntu 26.04 environment with two CPUs, at least 3 GiB
memory, and a matching Linux 7.0 image/headers. The model's `dma_map_ops` backend
uses the Linux 7.0 API; this test limitation is separate from driver support for
older kernels. `scripts/ci-linux.sh` installs the build/runtime dependencies.
For a standalone test environment:

```sh
sudo apt update
sudo apt install build-essential python3 busybox-static qemu-system-x86 cpio \
  linux-headers-generic linux-image-generic
```

Replace `7.0.0-38-generic` below with the installed matching test kernel release;
the container host's `uname -r` is not necessarily that kernel.

```sh
python3 tests/rx-lifecycle/run.py --kernel 7.0.0-38-generic
python3 tests/rx-lifecycle/run.py --kernel 7.0.0-38-generic --mode negative
python3 tests/rx-lifecycle/run.py --kernel 7.0.0-38-generic --mode startup-race
python3 tests/rx-lifecycle/run.py --kernel 7.0.0-38-generic --mode shutdown-race
```

QEMU defaults to TCG and does not need `/dev/kvm`; `--accel kvm` is optional.
Each invocation boots a diskless guest with two vCPUs, 1536 MiB RAM, no external
NIC, no shared directory and no device passthrough. Test modules load only in
that guest. A 300-second host timeout bounds each run. Logs and generated modules
stay under ignored `build/rx-page-pool/`; do not commit raw logs.

Normal mode requires exactly 63 successful cases, equal nonzero map/unmap
counts, zero live mappings, and no unexpected diagnostics. Negative mode must
reject all 16 frames when CPU synchronization is deliberately skipped. An
unexpected diagnostic, failed assertion, or incomplete guest causes failure.
`startup-race` runs two cases: the modeled peer completes the first frame while
process-context allocation of the second frame is delayed. The ownership
checker rejects duplicate submissions of a frame still queued/completed.
`shutdown-race` runs one case: a stopper on another CPU quiesces RX while the
poll tail is delayed after NAPI ownership is released. Ring stop must wait for
that tail. Both cases also run in normal mode; zero-budget polling must leave
RX indices and mapping counts unchanged. Startup tests include completions during initial ring priming.

## Debug kernels and detector controls

In a disposable Linux environment, additionally install `flex bison bc libssl-dev
libelf-dev ca-certificates`. The builder needs Python >= 3.12, downloads Linux 7.0 over HTTPS and
verifies a pinned SHA-256 before extraction. It does not verify a PGP signature.
It builds two kernels without installing them. Allow several GiB of disk space.

```sh
python3 tests/rx-lifecycle/build-kernels.py --jobs 3
for kind in kasan kcsan; do
  python3 tests/rx-lifecycle/run.py \
    --kdir "build/debug-kernels/$kind" \
    --image "build/debug-kernels/$kind/arch/x86/boot/bzImage" \
    --output "build/ring-$kind"
  python3 tests/rx-lifecycle/run.py \
    --kdir "build/debug-kernels/$kind" \
    --image "build/debug-kernels/$kind/arch/x86/boot/bzImage" \
    --mode "sanity-$kind" --output "build/control-$kind"
done
python3 tests/rx-lifecycle/run.py \
  --kdir build/debug-kernels/kasan \
  --image build/debug-kernels/kasan/arch/x86/boot/bzImage \
  --mode sanity-dma --output build/control-dma
```

KCSAN requires runtime activation as well as configuration; the runner passes
`kcsan.early_enable=1` and requires the activation message. Its sampling settings
are `skip_watch=100`, `udelay_task=50`. Controls require a KASAN use-after-free,
a KCSAN race, or a mismatched-size DMA API report, respectively. Run each in a
separate guest; intentional control reports are never counted as normal passes.
These debug builds/controls are manual tests, not part of every hosted
CI run. See [results and scope](../../docs/validation.md).
