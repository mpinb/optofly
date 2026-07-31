#!/usr/bin/env python3
"""Zip a Braid ``.braid`` recording folder into a ``.braidz`` file by hand.

Pure-Python reimplementation of the ``braidz-writer`` Rust crate
(``strand-braid/braid/braidz-writer``), for recovering a crashed Braid
recording without needing the Rust toolchain. Normally Braid zips its
``.braid`` working folder into a ``.braidz`` file itself when a recording
stops cleanly; if Braid or the machine crashes mid-recording, that step
never runs and the raw folder is left behind. This script does that step
by hand, stdlib only.

Produces the same archive layout as the Rust crate: a human-readable text
header (so opening the file in a text editor shows something sensible), then
a plain ZIP (no compression - the ``.csv.gz`` members inside are already
compressed) with paths relative to ``src_dir`` and no leading directory name,
and ``README.md`` moved first if present.

Run with:
    python3 scripts/braidz_writer.py /mnt/data/experiments/<timestamp>.braid
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

HEADER = (
    b"BRAIDZ file. This is a standard ZIP file with a "
    b"specific schema. You can view the contents of this "
    b"file at https://braidz.strawlab.org/\n"
)
README_FNAME = "README.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zip a Braid .braid recording folder into a .braidz file.",
    )
    parser.add_argument("src_dir", type=Path, help="The crashed/leftover .braid folder.")
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help=(
            "Destination .braidz path. Defaults to src_dir with '.braid' "
            "replaced by '.braidz' (or '.braidz' appended if src_dir has no "
            "'.braid' extension)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing destination file instead of refusing.",
    )
    return parser.parse_args()


def default_dest(src_dir: Path) -> Path:
    # Deliberately not matching upstream braidz-writer-cli's default naming
    # here: its add_extension() helper turns "foo.braid" into
    # "foo.braid.braidz" (it appends rather than replaces), which is
    # surprising and doesn't match the clean "foo.braidz" name Braid itself
    # writes on a normal shutdown. This replicates Braid's own convention
    # instead - pass --dest explicitly if you want something else.
    if src_dir.suffix == ".braid":
        return src_dir.with_suffix(".braidz")
    return src_dir.with_name(src_dir.name + ".braidz")


def dir_to_braidz(src_dir: Path, dest: Path) -> None:
    """Zip src_dir into dest, matching the layout Braid itself writes."""
    entries = sorted(src_dir.rglob("*"))
    readme = src_dir / README_FNAME
    if readme in entries:
        entries.remove(readme)
        entries.insert(0, readme)

    with open(dest, "wb") as f:
        f.write(HEADER)
        with zipfile.ZipFile(
            f, mode="a", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as zf:
            for path in entries:
                name = "/".join(path.relative_to(src_dir).parts)
                if path.is_dir():
                    zf.writestr(name + "/", b"")
                else:
                    zf.write(path, arcname=name)


def main() -> None:
    args = parse_args()
    src_dir: Path = args.src_dir
    if not src_dir.is_dir():
        print(f"Not a directory: {src_dir}", file=sys.stderr)
        sys.exit(1)

    dest: Path = args.dest if args.dest is not None else default_dest(src_dir)
    if dest.exists() and not args.force:
        print(f"Refusing to overwrite existing file: {dest} (use --force)", file=sys.stderr)
        sys.exit(1)

    dir_to_braidz(src_dir, dest)
    print(f"Wrote {dest}")


if __name__ == "__main__":
    main()
