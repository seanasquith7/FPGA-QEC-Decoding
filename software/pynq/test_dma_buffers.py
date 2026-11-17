"""Local tests validate ownership logic, not DDR coherency or hardware DMA."""

import builtins
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from dma_buffers import allocate_local, allocate_pynq, prepare_buffers


class BufferTests(unittest.TestCase):
    def setUp(self):
        self.payload = np.array([1, 2, 3, 255], dtype=np.uint8)
        self.allocated = []

    def allocator(self, **kwargs):
        buffer = allocate_local(**kwargs)
        self.allocated.append(buffer)
        return buffer

    def pair(self, **kwargs):
        return prepare_buffers(self.payload, output_nbytes=7, allocator=self.allocator, **kwargs)

    def test_exact_copy_initialization_and_release(self):
        with self.pair() as pair:
            np.testing.assert_array_equal(pair.tx, self.payload)
            self.assertTrue(np.all(pair.rx == 0))
            self.assertEqual(pair.rx.nbytes, 7)
            self.assertFalse(hasattr(pair.tx, "device_address"))
            self.payload[:] = 0
            np.testing.assert_array_equal(pair.tx, [1, 2, 3, 255])
        self.assertTrue(all(b.released for b in self.allocated))
        pair.close()
        self.assertTrue(all(b.events.count("freebuffer") == 1 for b in self.allocated))
        with self.assertRaises(RuntimeError):
            pair.reserve_for_dma()

    def test_sync_order_valid_length_and_output_copy(self):
        with self.pair() as pair:
            with self.assertRaises(RuntimeError):
                pair.output_copy()
            pair.reserve_for_dma()
            self.assertEqual(pair.tx.events, ["sync_to_device"])
            self.assertEqual(pair.rx.events, ["sync_to_device"])
            # Simulate a device write; no actual PL access occurs.
            pair.rx[:] = [11, 12, 13, 200, 201, 202, 203]
            pair.release_after_dma_stopped(received_nbytes=3)
            self.assertEqual(pair.rx.events, ["sync_to_device", "sync_from_device"])
            result = pair.output_copy()
        self.assertIs(type(result), np.ndarray)
        np.testing.assert_array_equal(result, [11, 12, 13])

    def test_active_buffers_cannot_be_freed(self):
        pair = self.pair()
        pair.reserve_for_dma()
        for operation in (pair.close, pair.output_copy, pair.reserve_for_dma):
            with self.assertRaises(RuntimeError):
                operation()
        self.assertFalse(any(b.released for b in self.allocated))
        pair.release_after_dma_stopped()
        with self.assertRaises(RuntimeError):
            pair.output_copy()
        pair.close()

    def test_bad_completion_lengths_keep_ownership_reserved(self):
        pair = self.pair()
        pair.reserve_for_dma()
        for length in (-1, 8, True, 2.5):
            with self.subTest(length=length), self.assertRaises(ValueError):
                pair.release_after_dma_stopped(received_nbytes=length)
        with self.assertRaises(RuntimeError):
            pair.close()
        pair.release_after_dma_stopped(received_nbytes=0)
        self.assertEqual(pair.output_copy().size, 0)
        pair.close()

    def test_context_exception_releases_idle_allocations(self):
        with self.assertRaisesRegex(ValueError, "caller failed"):
            with self.pair():
                raise ValueError("caller failed")
        self.assertTrue(all(b.released for b in self.allocated))

    def test_partial_allocation_failure_frees_input(self):
        def fail_second(**kwargs):
            if self.allocated:
                raise MemoryError("No more contiguous memory")
            return self.allocator(**kwargs)
        with self.assertRaises(MemoryError):
            prepare_buffers(self.payload, output_nbytes=7, allocator=fail_second)
        self.assertTrue(self.allocated[0].released)

    def test_incompatible_allocation_frees_all_owned_buffers(self):
        def wrong_dtype(**kwargs):
            if self.allocated:
                kwargs["dtype"] = np.uint16
            return self.allocator(**kwargs)
        with self.assertRaises(TypeError):
            prepare_buffers(self.payload, output_nbytes=7, allocator=wrong_dtype)
        self.assertTrue(all(b.released for b in self.allocated))

    def test_input_capacity_and_budget_validation_before_allocation(self):
        for payload in (np.array([], dtype=np.uint8), np.zeros(4, dtype=np.uint32),
                        self.payload[::2], self.payload.reshape(2, 2), b"1234"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                prepare_buffers(payload, output_nbytes=7, allocator=self.allocator)
        for length in (0, -1, True, 1.5):
            with self.subTest(length=length), self.assertRaises(ValueError):
                prepare_buffers(self.payload, output_nbytes=length, allocator=self.allocator)
        with self.assertRaises(ValueError):
            self.pair(max_total_nbytes=10)
        self.assertEqual(len(self.allocated), 0)
        with self.pair(max_total_nbytes=11):
            pass

    def test_pynq_adapter_has_no_silent_fallback(self):
        original_import = builtins.__import__
        def missing_pynq(name, *args, **kwargs):
            if name == "pynq":
                raise ImportError("Not installed")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=missing_pynq):
            with self.assertRaisesRegex(RuntimeError, "PYNQ is unavailable"):
                allocate_pynq(shape=(4,), dtype=np.uint8)

    def test_cli_local_smoke(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "payload.bin"
            path.write_bytes(self.payload.tobytes())
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("dma_buffers.py")),
                "--payload", str(path), "--output-bytes", "7", "--backend", "local"],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOCAL TEST ONLY", result.stdout)
        self.assertIn("Both buffers released", result.stdout)


if __name__ == "__main__":
    unittest.main()
