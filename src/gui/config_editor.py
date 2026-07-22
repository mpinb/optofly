"""tomlkit-based read/write helpers for the Config tab's common-subset forms.

Unlike the Advanced tab (which round-trips the whole file as raw text via
tomllib for validation), this module edits only a declared list of dotted
field paths and preserves every comment and untouched key in the file —
tomlkit keeps the original document structure, tomllib/tomli-w would not.
"""

import os
from typing import Any

import tomlkit

_MISSING = object()

# (dotted path as a tuple, python type) — the only keys the Config tab may read/write.
CONFIG_TOML_FIELDS: list[tuple[tuple[str, ...], type]] = [
    (("opto_trigger", "active"), bool),
    (("opto_trigger", "color"), str),
    (("opto_trigger", "duration"), list),
    (("opto_trigger", "intensity"), list),
    (("opto_trigger", "frequency"), float),
    (("opto_trigger", "sham_probability"), float),
    (("camera", "active"), bool),
    (("visual_stimuli", "active"), bool),
    (("monitoring", "active"), bool),
]

VISUAL_STIMULI_TOML_FIELDS: list[tuple[tuple[str, ...], type]] = [
    (("visual_stimuli", "background", "enabled"), bool),
    (("visual_stimuli", "looming", "enabled"), bool),
    (("visual_stimuli", "looming", "sham_probability"), float),
    (("visual_stimuli", "oscillating_square", "enabled"), bool),
]


def _get_path(doc, path: tuple[str, ...]):
    node = doc
    for key in path:
        if not hasattr(node, "get"):
            return _MISSING
        node = node.get(key, _MISSING)
        if node is _MISSING:
            return _MISSING
    return node


def load_fields(path: str, fields: list[tuple[tuple[str, ...], type]]) -> dict[str, Any]:
    """Read the declared dotted TOML paths from `path`.

    Returns a flat dict keyed by dotted path (e.g. "opto_trigger.color");
    a field missing from the file maps to None.
    """
    with open(path, "r") as f:
        doc = tomlkit.parse(f.read())

    result = {}
    for toml_path, _field_type in fields:
        value = _get_path(doc, toml_path)
        result[".".join(toml_path)] = None if value is _MISSING else value
    return result


def save_fields(
    path: str,
    fields: list[tuple[tuple[str, ...], type]],
    updates: dict[str, Any],
) -> None:
    """Write `updates` (keyed by dotted path) into the TOML file at `path`.

    Only the declared `fields` may be written — comments and every other key
    in the file are preserved untouched. Writes via a temp file + os.replace
    so a failure never leaves a half-written file on disk.
    """
    valid_paths = {".".join(toml_path) for toml_path, _ in fields}
    for dotted_path in updates:
        if dotted_path not in valid_paths:
            raise ValueError(f"{dotted_path!r} is not an editable field")

    with open(path, "r") as f:
        doc = tomlkit.parse(f.read())

    for dotted_path, value in updates.items():
        *parents, leaf = dotted_path.split(".")
        node = doc
        for key in parents:
            node = node[key]
        node[leaf] = value

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        f.write(tomlkit.dumps(doc))
    os.replace(tmp_path, path)


def coerce_form_value(raw: str | None, field_type: type) -> Any:
    """Convert a raw HTML form value into the type a TOML field expects.

    bool fields come from a checkbox: "on" when checked, None (absent) when not.
    list fields come from a single comma-separated text input, e.g. "100,200,300".
    """
    if field_type is bool:
        return raw == "on"
    if field_type is list:
        return [int(part.strip()) for part in (raw or "").split(",") if part.strip()]
    if field_type is float:
        return float(raw)
    return raw
