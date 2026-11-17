"""Stage 2.2 buffer ownership and synchronization; no DMA operations."""

import argparse
from contextlib import ExitStack
from pathlib import Path

import numpy as np


def allocate_pynq(*, shape, dtype, target=None):
    """Allocate on PYNQ hardware. Never fall back to ordinary NumPy memory."""
    try:
        from pynq import allocate
    except ImportError as exc:
        raise RuntimeError("PYNQ is unavailable. Use the explicit local backend for local tests.") from exc
    return allocate(shape=shape, dtype=dtype, target=target)


class LocalTestBuffer(np.ndarray):
    """An explicit test double; no DMA address or physical-contiguity claim."""

    def __new__(cls, shape, dtype):
        obj = np.zeros(shape, dtype=dtype).view(cls)
        obj.events = []
        obj.released = False
        return obj

    def sync_to_device(self):
        self.events.append("sync_to_device")

    def sync_from_device(self):
        self.events.append("sync_from_device")

    def freebuffer(self):
        self.events.append("freebuffer")
        self.released = True


def allocate_local(*, shape, dtype):
    """Return a local test double, not a PYNQ or DMA-compatible allocation."""
    return LocalTestBuffer(shape, dtype)


class BufferPair:
    """Own two allocations. The caller must report actual DMA ownership.

    No method starts, polls, waits for, or stops DMA. Call release_after_dma_stopped
    only after all hardware access to both allocations has stopped. This is a
    caller assertion, not a hardware completion check.
    """

    def __init__(self, tx, rx, resources):
        self.tx = tx
        self.rx = rx
        self._resources = resources
        self._closed = False
        self._in_use = False
        self._received_nbytes = None

    def _require_open(self):
        if self._closed:
            raise RuntimeError("Buffers are closed.")

    def reserve_for_dma(self):
        """Synchronize initialized buffers, then reserve them before DMA submission."""
        self._require_open()
        if self._in_use:
            raise RuntimeError("Buffers are already reserved for DMA.")
        self._received_nbytes = None
        self.tx.sync_to_device()
        # Flush CPU initialization before PL may write the receive allocation.
        self.rx.sync_to_device()
        self._in_use = True

    def release_after_dma_stopped(self, *, received_nbytes=None):
        """Record stopped hardware access and an optional verified receive length.

        None means aborted/no valid output. Use a length obtained from the future
        DMA adapter; buffer capacity alone is not an actual received length.
        """
        self._require_open()
        if not self._in_use:
            raise RuntimeError("Buffers were not reserved for DMA.")
        if received_nbytes is not None and (
            type(received_nbytes) is not int or not 0 <= received_nbytes <= self.rx.nbytes
        ):
            raise ValueError("received_nbytes must be an integer within receive capacity.")
        self._in_use = False
        if received_nbytes is not None:
            self.rx.sync_from_device()
            self._received_nbytes = received_nbytes

    def output_copy(self):
        """Return owned raw bytes only after a valid completed receive was reported."""
        self._require_open()
        if self._in_use or self._received_nbytes is None:
            raise RuntimeError("No completed receive with a verified length is available.")
        # A plain copy must remain valid after the physical allocation is freed.
        return np.array(self.rx[:self._received_nbytes], dtype=np.uint8, copy=True)

    def close(self):
        if self._closed:
            return
        if self._in_use:
            raise RuntimeError("Stop all DMA access and release ownership before freeing buffers.")
        try:
            self._resources.close()
        finally:
            self._closed = True
            # Do not retain freed PYNQ array references through this owner.
            self.tx = None
            self.rx = None

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


def prepare_buffers(payload, *, output_nbytes, allocator, max_total_nbytes=None):
    """Copy exact uint8 payload bytes into owned input/output allocations.

    output_nbytes is a capacity supplied by the caller, never an inferred
    correction size. max_total_nbytes is an optional allocation budget, not a
    DMA transfer-size limit. The allocator is always explicitly selected.
    """
    if (not isinstance(payload, np.ndarray) or payload.dtype != np.uint8
            or payload.ndim != 1 or payload.size == 0 or not payload.flags.c_contiguous):
        raise ValueError("payload must be a nonempty, 1D, C-contiguous NumPy uint8 array.")
    if type(output_nbytes) is not int or output_nbytes < 1:
        raise ValueError("output_nbytes must be a positive integer.")
    if max_total_nbytes is not None:
        if type(max_total_nbytes) is not int or max_total_nbytes < 1:
            raise ValueError("max_total_nbytes must be a positive integer.")
        if payload.nbytes + output_nbytes > max_total_nbytes:
            raise ValueError("Requested allocations exceed max_total_nbytes.")

    with ExitStack() as resources:
        buffers = []
        for length in (payload.nbytes, output_nbytes):
            buffer = allocator(shape=(length,), dtype=np.uint8)
            if not callable(getattr(buffer, "freebuffer", None)):
                raise TypeError("Allocator must return an owned buffer with freebuffer().")
            resources.callback(buffer.freebuffer)
            if (not isinstance(buffer, np.ndarray) or buffer.dtype != np.uint8
                    or buffer.shape != (length,) or not buffer.flags.c_contiguous
                    or not buffer.flags.writeable
                    or any(not callable(getattr(buffer, method, None))
                           for method in ("sync_to_device", "sync_from_device"))):
                raise TypeError("Allocator returned an incompatible buffer.")
            buffers.append(buffer)
        tx, rx = buffers
        if np.shares_memory(tx, rx):
            raise ValueError("Input and output buffers must not overlap.")
        tx[:] = payload
        rx[:] = 0
        return BufferPair(tx, rx, resources.pop_all())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output-bytes", type=int, required=True,
                        help="Explicit receive capacity, not an assumed correction format.")
    parser.add_argument("--backend", choices=("local", "pynq"), required=True)
    args = parser.parse_args()
    try:
        payload = np.frombuffer(args.payload.read_bytes(), dtype=np.uint8).copy()
        allocator = allocate_local if args.backend == "local" else allocate_pynq
        with prepare_buffers(payload, output_nbytes=args.output_bytes, allocator=allocator) as buffers:
            np.testing.assert_array_equal(np.asarray(buffers.tx), payload)
            print(f"Input bytes: {buffers.tx.nbytes}; output capacity: {buffers.rx.nbytes}")
            if args.backend == "local":
                print("LOCAL TEST ONLY: ordinary NumPy allocations; no physical DDR or DMA.")
            else:
                print("PYNQ buffers allocated. No overlay loaded or DMA transfer performed.")
        print("Both buffers released.")
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
