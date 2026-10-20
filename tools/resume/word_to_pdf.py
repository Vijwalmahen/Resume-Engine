#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fcntl
import shutil
from pathlib import Path

from docx2pdf import convert


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    source = Path(args.input).resolve()
    destination = Path(args.output).resolve()

    if not source.exists():
        raise SystemExit(f"Input DOCX does not exist: {source}")

    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    staging = (
        Path.home()
        / "Documents"
        / "ResumeRenderer"
    )

    staging.mkdir(
        parents=True,
        exist_ok=True
    )

    # SAME authorized file every time.
    staged_docx = staging / "resume-input.docx"
    staged_pdf = staging / "resume-input.pdf"

    # SAME lock used by resume rendering.
    lock_path = staging / ".word-conversion.lock"

    with lock_path.open("w") as lock:

        fcntl.flock(
            lock.fileno(),
            fcntl.LOCK_EX
        )

        try:
            shutil.copy2(
                source,
                staged_docx
            )

            staged_pdf.unlink(
                missing_ok=True
            )

            try:
                convert(
                    str(staged_docx),
                    str(staged_pdf),
                    keep_active=True
                )
            except TypeError:
                convert(
                    str(staged_docx),
                    str(staged_pdf)
                )

            if not staged_pdf.exists():
                raise RuntimeError(
                    "Microsoft Word failed to create the PDF."
                )

            shutil.copy2(
                staged_pdf,
                destination
            )

        finally:
            # Keep resume-input.docx because macOS/Word
            # has already authorized this exact pathname.
            staged_pdf.unlink(
                missing_ok=True
            )

            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_UN
            )

    print(destination)


if __name__ == "__main__":
    main()
