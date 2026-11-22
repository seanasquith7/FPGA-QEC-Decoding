"""Offline Stage 2 preparation. No PYNQ import, DDR allocation, or DMA access."""

import argparse
from dataclasses import asdict, dataclass
import io
import hashlib
import json
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class PackingConfig:
    input_semantics: str
    word_bits: int
    bit_order: str
    byte_order: str
    column_order: tuple[int, ...]
    record_order: str
    profile_status: str

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict) or set(values) != set(cls.__dataclass_fields__):
            raise ValueError("Config must contain exactly the documented packing fields.")
        if not isinstance(values["column_order"], list):
            raise ValueError("column_order must be a JSON list of source bit indices.")
        return cls(**{**values, "column_order": tuple(values["column_order"])})

    def validate(self, width):
        if self.input_semantics not in ("detector_events", "raw_syndrome_rounds"):
            raise ValueError("Unknown input_semantics.")
        if type(self.word_bits) is not int or self.word_bits not in (8, 16, 32, 64):
            raise ValueError("word_bits must be 8, 16, 32, or 64.")
        if self.bit_order not in ("lsb-first", "msb-first"):
            raise ValueError("bit_order must be lsb-first or msb-first.")
        if self.byte_order not in ("little", "big"):
            raise ValueError("byte_order must be little or big.")
        if self.record_order not in ("shot-major", "round-major"):
            raise ValueError("record_order must be shot-major or round-major.")
        if self.input_semantics == "detector_events" and self.record_order != "shot-major":
            raise ValueError("Detector vectors are whole-shot records, not raw rounds.")
        if self.profile_status != "local-test-only":
            raise ValueError("Only local-test-only profiles are supported at this checkpoint.")
        if (any(type(i) is not int for i in self.column_order)
                or sorted(self.column_order) != list(range(width))):
            raise ValueError("column_order must be a complete permutation of source bits.")


def _positive_int(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def load_stage1(dataset):
    """Verify Stage 1 files and return only detector inputs, never truth labels."""
    bits, source, _, _ = _load_stage1(Path(dataset))
    return bits, source


def _load_stage1(dataset):
    metadata_bytes = (dataset / "metadata.json").read_bytes()
    metadata = json.loads(metadata_bytes)
    if metadata.get("schema_version") != 1 or metadata.get("stage") != "test_data_generation":
        raise ValueError("Expected a Stage 1 schema-version-1 dataset.")
    shots = _positive_int(metadata["parameters"]["shots"], "shots")
    width = _positive_int(metadata["num_detectors"], "num_detectors")
    _positive_int(metadata["num_observables"], "num_observables")
    hashes = metadata.get("files_sha256", {})
    detector_bytes = None
    for name in ("detectors.01", "observables.01", "circuit.stim", "detector_error_model.dem"):
        data = (dataset / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != hashes.get(name):
            raise ValueError(f"Missing or mismatched SHA-256: {name}")
        if name == "detectors.01":
            detector_bytes = data
        elif name == "circuit.stim":
            circuit_bytes = data
    rows = detector_bytes.split(b"\n")
    if rows[-1] != b"" or len(rows) != shots + 1:
        raise ValueError("Detector row count or LF termination does not match metadata.")
    if any(len(row) != width or set(row) - {48, 49} for row in rows[:-1]):
        raise ValueError("Detector rows must contain exactly num_detectors ASCII bits.")
    bits = np.frombuffer(b"".join(rows[:-1]), dtype=np.uint8).reshape(shots, width) - 48
    return bits, {
        "input_semantics": "detector_events",
        "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "detectors_sha256": hashes["detectors.01"],
        "circuit_sha256": hashes["circuit.stim"],
        "shots": shots, "num_detectors": width,
    }, metadata, circuit_bytes


def pack_records(bits, config):
    """Pack whole-shot detectors (2D) or caller-supplied raw rounds (3D).

    Raw arrays must already have shape (shots, rounds, bits_per_round). This
    function only reorders and packs; it never computes or inverts defects.
    Every record starts a new word and has zero padding in unused word bits.
    """
    bits = np.asarray(bits)
    dimensions = 2 if config.input_semantics == "detector_events" else 3
    if bits.ndim != dimensions or any(size == 0 for size in bits.shape):
        raise ValueError(f"{config.input_semantics} requires a nonempty {dimensions}D array.")
    if bits.dtype.kind not in "biu" or not np.all((bits == 0) | (bits == 1)):
        raise ValueError("Inputs must contain only integer or boolean 0/1 bits.")
    config.validate(bits.shape[-1])
    shots, width = bits.shape[0], bits.shape[-1]
    rounds = 1 if bits.ndim == 2 else bits.shape[1]
    arranged = bits[:, None, :] if bits.ndim == 2 else bits
    if config.record_order == "round-major":
        arranged = arranged.transpose(1, 0, 2)
    records = arranged.reshape(-1, width)[:, config.column_order]
    words_per_record = (width + config.word_bits - 1) // config.word_bits
    words = np.zeros((shots * rounds, words_per_record), dtype=np.uint64)
    for column in range(width):
        position = column % config.word_bits
        if config.bit_order == "msb-first":
            position = config.word_bits - 1 - position
        words[:, column // config.word_bits] |= (
            records[:, column].astype(np.uint64) << np.uint64(position))
    dtype = np.dtype(("<" if config.byte_order == "little" else ">")
                     + f"u{config.word_bits // 8}")
    # uint8 is an explicit memory byte sequence, independent of host endianness.
    payload = np.frombuffer(words.astype(dtype).tobytes(), dtype=np.uint8).copy()
    record_bytes = words_per_record * dtype.itemsize
    layout = {
        "schema_version": 1,
        "hardware_ready": False,
        "buffer_kind": "ordinary C-contiguous NumPy uint8; NOT physically contiguous DDR",
        "config": {**asdict(config), "column_order": list(config.column_order)},
        "shots": shots,
        "records_per_shot": rounds,
        "bits_per_record": width,
        "words_per_record": words_per_record,
        "bytes_per_record": record_bytes,
        "padding_bits_per_record": words_per_record * config.word_bits - width,
        "padding_value": 0,
        "payload_nbytes": payload.nbytes,
        "payload_sha256": hashlib.sha256(payload.tobytes()).hexdigest(),
        "record_index_formula": ("shot * records_per_shot + round"
                                 if config.record_order == "shot-major"
                                 else "round * shots + shot"),
        "record_byte_offset_formula": "record_index * bytes_per_record",
        "framing": "No headers, shot IDs, TLAST markers or hardware framing are added.",
        "axis_data_width_bits": None,
        "dma_transfer_boundaries": None,
        "output_correction_encoding": None,
    }
    return payload, layout


def prepare_stage1(dataset, config):
    dataset = Path(dataset)
    bits, source, metadata, circuit_bytes = _load_stage1(dataset)
    if config.input_semantics == "raw_syndrome_rounds":
        mapping = metadata.get("raw_syndrome")
        if not mapping:
            raise ValueError("Dataset has detector events, not raw syndrome rounds. Regenerate it.")
        rounds = _positive_int(metadata["parameters"]["rounds"], "rounds")
        from generate_test_data import syndrome_mapping
        import stim
        circuit = stim.Circuit(circuit_bytes.decode("utf-8"))
        if mapping != syndrome_mapping(circuit, rounds):
            raise ValueError("Raw syndrome mapping differs from the saved circuit.")
        data = (dataset / "raw_syndrome.npy").read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != metadata["files_sha256"].get("raw_syndrome.npy"):
            raise ValueError("Missing or mismatched SHA-256: raw_syndrome.npy")
        bits = np.load(io.BytesIO(data), allow_pickle=False)
        if bits.shape != (source["shots"], rounds, 8):
            raise ValueError("Raw syndrome shape must be (shots, rounds, 8).")
        source.update(input_semantics=config.input_semantics, raw_sha256=digest, **mapping)
    payload, layout = pack_records(bits, config)
    layout["source"] = source
    return payload, layout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("Output already exists; choose a new directory.")
        config = PackingConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
        payload, layout = prepare_stage1(args.dataset, config)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "payload.bin").write_bytes(payload.tobytes())
        (args.output / "layout.json").write_text(
            json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"Prepared {layout['shots']} shots, {payload.nbytes} bytes.")
    print("LOCAL TEST ONLY: no physical DDR allocation, overlay loading or DMA transfer.")


if __name__ == "__main__":
    main()
