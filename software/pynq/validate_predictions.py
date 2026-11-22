"""Compare normalized decoder predictions with Stim logical observable flips."""

import argparse
import hashlib
import json
from pathlib import Path


def unique_fields(pairs):
    """Reject ambiguous JSON records before a repeated field can replace its value."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def load_truth(dataset):
    metadata_bytes = (dataset / "metadata.json").read_bytes()
    metadata = json.loads(metadata_bytes, object_pairs_hook=unique_fields)
    if (not isinstance(metadata, dict) or metadata.get("stage") != "test_data_generation"
            or type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1):
        raise ValueError("Expected a Stage 1 dataset with schema version 1.")
    shots = metadata["parameters"]["shots"]
    if (type(metadata["num_observables"]) is not int or metadata["num_observables"] != 1
            or type(shots) is not int or shots < 1):
        raise ValueError("Validation requires one logical observable and a positive shot count.")
    data = (dataset / "observables.01").read_bytes()
    if hashlib.sha256(data).hexdigest() != metadata["files_sha256"]["observables.01"]:
        raise ValueError("Logical truth SHA-256 mismatch.")
    rows = data.split(b"\n")
    if len(rows) != shots + 1 or rows[-1] or any(r not in (b"0", b"1") for r in rows[:-1]):
        raise ValueError("Expected one LF-terminated logical bit per shot.")
    return [int(r) for r in rows[:-1]], hashlib.sha256(metadata_bytes).hexdigest()


def load_predictions(path, dataset_id, shots, decoder):
    document = json.loads(Path(path).read_bytes(), object_pairs_hook=unique_fields)
    if not isinstance(document, dict) or document.get("semantics") != "logical_flip":
        raise ValueError("Expected normalized logical_flip predictions.")
    if document.get("decoder") != decoder or document.get("dataset_sha256") != dataset_id:
        raise ValueError("Decoder or dataset identity mismatch.")
    if document.get("source") not in ("synthetic", "software" if decoder == "software" else "hardware"):
        raise ValueError("Invalid prediction source.")
    rows = document.get("predictions")
    if not isinstance(rows, list) or len(rows) != shots:
        raise ValueError("Prediction count must equal the dataset shot count.")
    aligned = [None] * shots
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each prediction must be a record.")
        shot, bit = row.get("shot_id"), row.get("logical_flip")
        if type(shot) is not int or not 0 <= shot < shots or aligned[shot] is not None:
            raise ValueError("Invalid or duplicate shot ID.")
        if type(bit) is not int or bit not in (0, 1):
            raise ValueError("logical_flip must be integer 0 or 1.")
        aligned[shot] = bit
    return aligned, document["source"]


def validate(dataset, *, software=None, fpga=None):
    if software is None and fpga is None:
        raise ValueError("Supply at least one prediction file.")
    truth, dataset_id = load_truth(Path(dataset))
    shots = len(truth)
    report = {"schema_version": 1, "dataset_sha256": dataset_id, "shots": shots,
              "decoders": {}, "fpga_vs_software": None}
    predictions = {}
    for decoder, path in (("software", software), ("fpga", fpga)):
        if path is None:
            continue
        bits, source = load_predictions(path, dataset_id, shots, decoder)
        errors = sum(a != b for a, b in zip(bits, truth, strict=True))
        predictions[decoder] = bits
        report["decoders"][decoder] = {
            "source": source, "correct": shots - errors, "errors": errors,
            "accuracy": (shots - errors) / shots, "logical_error_rate": errors / shots,
        }
    if software is not None and fpga is not None:
        matches = sum(a == b for a, b in zip(predictions["software"], predictions["fpga"], strict=True))
        report["fpga_vs_software"] = {"matches": matches, "disagreements": shots - matches,
                                      "agreement": matches / shots}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--software", type=Path)
    parser.add_argument("--fpga", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = validate(args.dataset, software=args.software, fpga=args.fpga)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
