# Stage 2.2 — Buffer Ownership and Preparation

Status: locally tested ownership and synchronization logic with a lazy PYNQ allocation adapter. Physical DDR allocation, cache coherency, and DMA behavior have not been tested on RFSoC hardware. No overlay is loaded and no DMA transfer is implemented here.

## Purpose

`dma_buffers.py` accepts the exact one-dimensional `uint8` payload produced by Stage 2.1 and prepares separate input and output allocations. It preserves byte order without reinterpreting words. Output capacity must be explicitly supplied; no correction encoding or bytes-per-shot rule is assumed.

The caller selects an allocator explicitly:

- `allocate_local`: ordinary NumPy test buffers. Synchronization calls are recorded only; there is no physical address and no simulated FPGA result.
- `allocate_pynq`: imports `pynq.allocate` only when called, requests `uint8` buffers, and optionally forwards an explicit allocation target. It never falls back to NumPy when PYNQ is unavailable.

The standard PYNQ active allocation target is used when `target=None`. On the board, confirm that this target is accessible to the integrated DMA. A custom target can be supplied through `functools.partial(allocate_pynq, target=confirmed_target)`.

## API

```python
prepare_buffers(
    payload,
    output_nbytes=explicit_receive_capacity,
    allocator=allocate_local,
    max_total_nbytes=optional_allocation_budget,
)
```

The payload must be a nonempty C-contiguous 1D NumPy `uint8` array. `output_nbytes` must be a positive integer. `max_total_nbytes`, if supplied, checks the sum of input and output allocation sizes before allocating. It is an allocation budget, not a DMA maximum transfer length.

The function allocates two distinct writable buffers, copies the input exactly, and initializes output memory to zero. `ExitStack` releases already-owned allocations if a later allocation, validation, or initialization fails. Allocators must return owned NumPy-compatible buffers with `freebuffer`, `sync_to_device`, and `sync_from_device` methods. A malformed allocator that returns an object without `freebuffer` cannot provide the required ownership contract.

Use the returned `BufferPair` as a context manager. `close()` uses `freebuffer()` and is idempotent. Both release callbacks are attempted even if one raises. Release errors propagate; a failed hardware release is not retried automatically. Avoid retaining array references or views after closing the owner.

## Ownership and synchronization

| Method | Meaning |
| --- | --- |
| `reserve_for_dma()` | Synchronize CPU writes in input and zero-initialized output buffers, then reserve both allocations before submission |
| `release_after_dma_stopped(received_nbytes=n)` | Caller asserts that all DMA access to both buffers has stopped; synchronize output and record a verified receive length |
| `release_after_dma_stopped()` | Caller asserts stopped hardware access after an abort; no output becomes readable |
| `output_copy()` | Return an independent ordinary `uint8` copy of only the verified received bytes |
| `close()` | Free idle buffers; refuse to free allocations still reserved for DMA |

These methods do not submit, wait for, inspect, or stop DMA. The stopped state is a caller assertion. The future DMA adapter must check both channels before releasing ownership. On submission failure or timeout, it must stop/reset and verify all accesses have stopped before calling the abort-release method. A timeout by itself does not prove that it is safe to free memory.

Do not discard the owner, allow its allocations to be garbage-collected, or access buffer views while hardware owns the buffers. If context exit refuses to close an active pair, retain the owner, recover the DMA hardware, release ownership, and then close it. This module does not make unsafe asynchronous hardware usage safe automatically.

`received_nbytes` is not inferred from output capacity. A future adapter must obtain or verify the actual transfer length from the agreed protocol and DMA completion information. An out-of-range or noninteger length is rejected without releasing the reservation. A new reservation invalidates any previous output. A failed receive synchronization leaves output unavailable.

Output is opaque bytes only. There is no comparison with truth, reference decoder, or correction-mask interpretation. Those belong to Stage 3.

## Local smoke test

From the actual Git repository root, using the existing environment in the parent directory:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s software/pynq -v
..\.venv\Scripts\python.exe software\pynq\dma_buffers.py --payload software\pynq\output\packed_smoke_1000\payload.bin --output-bytes 16 --backend local
```

The 16-byte output capacity is an arbitrary local allocation test value, not a hardware correction format. The CLI verifies input copying and releases both allocations; it does not fabricate output, reserve buffers for a transfer, or call synchronization as if DMA occurred.

Expected smoke output for the previously exported payload:

```text
Input bytes: 4000; output capacity: 16
LOCAL TEST ONLY: ordinary NumPy allocations; no physical DDR or DMA.
Both buffers released.
```

If the payload does not exist, follow the Stage 2.1 export commands in `README_Stage2.md`. No additional local packages are required.

## Board allocation-only example

Run only on the configured RFSoC PYNQ environment. Replace `confirmed_output_capacity` with a capacity established by the team; it is intentionally not assigned here.

```python
import numpy as np
from dma_buffers import allocate_pynq, prepare_buffers

payload = np.fromfile("payload.bin", dtype=np.uint8)

with prepare_buffers(
    payload,
    output_nbytes=confirmed_output_capacity,
    allocator=allocate_pynq,
) as buffers:
    assert buffers.tx.nbytes == payload.nbytes
    assert buffers.rx.nbytes == confirmed_output_capacity
    np.testing.assert_array_equal(np.asarray(buffers.tx), payload)
    print("Input device address:", buffers.tx.device_address)
    print("Output device address:", buffers.rx.device_address)
    print("Allocation only; no DMA transfer performed.")
```

The CLI also accepts `--backend pynq` with an explicit `--output-bytes` value for allocation-only testing. It never falls back to local memory. This path is implemented but has not been executed against PYNQ in this development environment.

Before actual DMA use, confirm allocation target, address alignment, stream width, DRE, transfer-size limits, TLAST/TKEEP, channel completion behavior, and output length/encoding. Byte-preserving `uint8` allocation does not establish those interface properties or make the local packing profile hardware-approved.

## Local acceptance coverage

Tests cover exact copying, independent input ownership, zero initialization, capacity/budget checks, synchronization call order, valid output-length slicing, output-copy lifetime, reservation/free guards, exception cleanup, partial allocation failure, incompatible allocations, explicit PYNQ-unavailable errors, and the local CLI.

Synthetic output writes occur only in unit tests and are labeled as test data. They validate software ownership logic, not cache coherency or decoded corrections.

Recorded local acceptance on November 17, 2026: all 28 project tests passed, including ten new buffer-management tests. The existing 4,000-byte payload passed the local CLI copy/allocation/release smoke test with an explicitly chosen 16-byte test output capacity. No PYNQ hardware allocation or DMA transfer was executed.

Reference: https://pynq.readthedocs.io/en/v3.1/_modules/pynq/buffer.html
