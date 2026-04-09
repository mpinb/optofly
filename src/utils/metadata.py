"""
Experiment metadata collection and persistence.

Provides interactive prompts for experiment metadata (experimenter, cross, dates, etc.)
and writes them to experiment_data.toml in the braid recording folder.
"""

from datetime import datetime
from pathlib import Path
from typing import Any


def collect_metadata() -> dict[str, Any]:
    """
    Interactively prompt the user for experiment metadata.

    Returns a dict with keys: experimenter, cross, cross_date, f1_date, atr_date,
    experiment_date, n_flies, experiment_duration, notes.

    Empty input → "N/A" for string fields, field default for numeric fields.
    """
    print("\n" + "=" * 70)
    print("EXPERIMENT METADATA")
    print("=" * 70)
    print("(press Enter to skip any field, use N/A as placeholder)\n")

    metadata: dict[str, Any] = {}

    # String fields with N/A default
    string_fields = [
        ("experimenter", "Experimenter name"),
        ("cross", "Genetic cross / genotype"),
        ("cross_date", "Cross date (YYYY-MM-DD)"),
        ("f1_date", "F1 date (YYYY-MM-DD)"),
        ("atr_date", "ATR date (YYYY-MM-DD)"),
    ]

    for key, prompt_text in string_fields:
        user_input = input(f"{prompt_text:.<40} ").strip()
        metadata[key] = user_input if user_input else "N/A"

    # Experiment date (default to today)
    today = datetime.now().strftime("%Y-%m-%d")
    user_input = input(f"Experiment date (YYYY-MM-DD) [{today}]:........ ").strip()
    metadata["experiment_date"] = user_input if user_input else today

    # Numeric field: n_flies
    user_input = input("Number of flies:................................ ").strip()
    if user_input:
        try:
            metadata["n_flies"] = int(user_input)
        except ValueError:
            print("  ⚠ Invalid integer, storing as N/A")
            metadata["n_flies"] = "N/A"
    else:
        metadata["n_flies"] = "N/A"

    # Numeric field: experiment_duration (default 24)
    user_input = input("Experiment duration (hours) [24]:............. ").strip()
    if user_input:
        try:
            metadata["experiment_duration"] = float(user_input)
        except ValueError:
            print("  ⚠ Invalid number, using default 24")
            metadata["experiment_duration"] = 24.0
    else:
        metadata["experiment_duration"] = 24.0

    # Notes (can be multi-line, but for simplicity accept single line)
    user_input = input("Notes (brief notes about this experiment):.... ").strip()
    metadata["notes"] = user_input if user_input else "N/A"

    # Summary and confirmation
    print("\n" + "-" * 70)
    print("SUMMARY")
    print("-" * 70)
    for key, value in metadata.items():
        print(f"  {key:.<30} {value}")

    print("-" * 70)
    confirm = input("\nProceed with this metadata? [Y/n] ").strip().lower()
    if confirm and confirm not in ("y", "yes"):
        print("⚠ Metadata collection cancelled. Exiting.")
        raise KeyboardInterrupt("User cancelled metadata collection")

    print()
    return metadata


def write_metadata(metadata: dict[str, Any], braid_folder: str) -> None:
    """
    Write experiment metadata to experiment_data.toml in the braid folder.

    Args:
        metadata: Dict with experiment metadata fields
        braid_folder: Path to the .braid recording folder
    """
    braid_path = Path(braid_folder)
    output_file = braid_path / "experiment_data.toml"

    # Build TOML content manually (flat structure, simple types)
    toml_lines = ['# Experiment metadata collected at startup\n']

    for key, value in metadata.items():
        if isinstance(value, str):
            # Escape quotes in string values
            escaped_value = value.replace('"', '\\"')
            toml_lines.append(f'{key} = "{escaped_value}"\n')
        elif isinstance(value, (int, float)):
            toml_lines.append(f'{key} = {value}\n')
        else:
            # Fallback: convert to string
            toml_lines.append(f'{key} = "{str(value)}"\n')

    output_file.write_text("".join(toml_lines))
    print(f"✓ Metadata written to {output_file}")
