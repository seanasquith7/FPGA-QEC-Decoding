"""Stage 1: export paired raw rounds, detector events and logical observable flips.

The original simulation script is loaded unchanged. Optional circuit settings
are explicit overrides of its generated rotated-memory-Z experiment.
"""

import argparse
import contextlib
import hashlib
import io
import json
import math
import platform
import runpy
from pathlib import Path

import numpy as np
import stim


SOURCE = Path(__file__).resolve().parents[2] / "simulation" / "stim-circuit.py"
TASK = "surface_code:rotated_memory_z"


def load_experiment():
    """Reuse the team's circuit and defaults without changing its source file."""
    with contextlib.redirect_stdout(io.StringIO()):
        experiment = runpy.run_path(str(SOURCE))
    if not isinstance(experiment.get("circuit"), stim.Circuit):
        raise ValueError("The simulation script must define a Stim circuit.")
    return experiment


def syndrome_mapping(circuit, rounds):
    """Locate MR rounds in the generated distance-3 rotated-memory-Z circuit."""
    qubits = []
    indices = []
    offset = 0
    for instruction in circuit.flattened():
        if instruction.name == "MR":
            targets = [target.value for target in instruction.targets_copy()]
            if len(targets) != 8 or len(set(targets)) != 8:
                raise ValueError("Expected eight distinct stabilizer measurements per round.")
            if qubits and targets != qubits:
                raise ValueError("Stabilizer measurement order changed between rounds.")
            qubits = targets
            indices.append(list(range(offset, offset + 8)))
        offset += instruction.num_measurements
    if len(indices) != rounds:
        raise ValueError("Raw measurement round count differs from the requested rounds.")
    return {"stabilizer_qubit_ids": qubits, "measurement_indices": indices,
            "axes": ["shot", "round", "stabilizer"], "round_order": "chronological"}


def generate_dataset(output, *, shots=None, distance=None, rounds=None,
                     noise=None, seed=42):
    """Create a new dataset directory; never overwrite an existing directory."""
    experiment = load_experiment()
    shots = experiment["NUM_SHOTS"] if shots is None else shots
    distance = experiment["DISTANCE"] if distance is None else distance
    rounds = experiment["ROUNDS"] if rounds is None else rounds
    noise = experiment["NOISE"] if noise is None else noise
    for name, value, minimum in [("shots", shots, 1), ("distance", distance, 2),
                                 ("rounds", rounds, 1)]:
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}.")
    if not math.isfinite(noise) or not 0 <= noise <= 1:
        raise ValueError("noise must be finite and between 0 and 1.")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ValueError("seed must be an integer in [0, 2**64).")

    original_settings = (experiment["DISTANCE"], experiment["ROUNDS"],
                         experiment["NOISE"])
    if (distance, rounds, noise) == original_settings:
        circuit = experiment["circuit"]
        circuit_source = "original simulation circuit"
    else:
        # This override mirrors all four noise channels in the team's script.
        circuit = stim.Circuit.generated(
            TASK, distance=distance, rounds=rounds,
            after_clifford_depolarization=noise,
            after_reset_flip_probability=noise,
            before_measure_flip_probability=noise,
            before_round_data_depolarization=noise,
        )
        circuit_source = "explicit rotated_memory_z parameter override"
    if circuit.num_detectors == 0 or circuit.num_observables == 0:
        raise ValueError("The circuit must contain detectors and observables.")
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Output already exists; choose a new directory: {output}")

    measurements = circuit.compile_sampler(seed=seed).sample(shots)
    detectors, observables = circuit.compile_m2d_converter().convert(
        measurements=measurements, separate_observables=True)
    mapping = syndrome_mapping(circuit, rounds) if distance == 3 else None
    model = circuit.detector_error_model(decompose_errors=True)
    output.mkdir(parents=True, exist_ok=False)
    if mapping is not None:
        np.save(output / "raw_syndrome.npy", measurements[:, mapping["measurement_indices"]])
    for filename, data in [("detectors.01", detectors),
                           ("observables.01", observables)]:
        # Stim's 01 format: no header; one ASCII bit string per shot, LF ending.
        with (output / filename).open("w", encoding="ascii", newline="\n") as f:
            np.savetxt(f, data, fmt="%d", delimiter="", newline="\n")
    (output / "circuit.stim").write_text(str(circuit) + "\n", encoding="utf-8")
    (output / "detector_error_model.dem").write_text(str(model) + "\n", encoding="utf-8")
    metadata = {
        "schema_version": 1,
        "stage": "test_data_generation",
        "source": {
            "repository": "https://github.com/seanasquith7/FPGA-QEC-Decoding",
            "path": "simulation/stim-circuit.py",
            "sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "circuit_source": circuit_source,
        },
        "parameters": {"distance": distance, "rounds": rounds,
                       "noise_per_channel": noise, "shots": shots, "seed": seed},
        "raw_syndrome": mapping,
        "num_detectors": circuit.num_detectors,
        "num_observables": circuit.num_observables,
        "format": {
            "name": "Stim 01", "encoding": "ASCII", "line_ending": "LF",
            "header": False, "row_order": "shot 0, shot 1, ...",
            "detector_columns": "left to right: D0, D1, ...",
            "observable_columns": "left to right: L0, L1, ...",
            "pairing": "The same row index identifies the same shot in both files.",
            "transport_protocol": "not defined in Stage 1",
        },
        "truth_semantics": "Observable flips relative to the noiseless reference; not decoded failures.",
        "summary": {
            "shots_with_detection_events": int(np.any(detectors, axis=1).sum()),
            "shots_with_observable_flips": int(np.any(observables, axis=1).sum()),
        },
        "environment": {"stim": stim.__version__, "numpy": np.__version__,
                        "python": platform.python_version(),
                        "platform": platform.platform(), "machine": platform.machine()},
        "reproducibility": "Same seed requires the same Stim version, machine capabilities and sampling call sequence; one batch is used here.",
        "files_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(output.iterdir()) if p.is_file()},
    }
    # Metadata is written last and marks completion of the dataset export.
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8",
    )
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="A new output directory (existing paths are refused).")
    parser.add_argument("--shots", type=int)
    parser.add_argument("--distance", type=int)
    parser.add_argument("--rounds", type=int)
    parser.add_argument("--noise", type=float)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        metadata = generate_dataset(**vars(args))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"Saved {metadata['parameters']['shots']} paired shots to {args.output}")
    print(f"Detectors per shot: {metadata['num_detectors']}")
    print(f"Observables per shot: {metadata['num_observables']}")
    print("Stage 1 complete. No decoding or hardware transfer was performed.")


if __name__ == "__main__":
    main()
