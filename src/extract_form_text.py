"""Extract plain text from the Form ADV Part 1A PDF for building the field dictionary.

One-off helper: the SEC publishes Form ADV only as a PDF, and the bulk CSV's
column headers are bare item codes (e.g. "5F(2)(c)"), so we need the form's own
wording to label them.
"""

import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "reference" / "formadv-part1a_1.pdf"
OUT = ROOT / "reference" / "formadv-part1a.txt"


def main() -> int:
    if not PDF.exists():
        print(f"missing {PDF}", file=sys.stderr)
        return 1

    reader = PdfReader(PDF)
    chunks = []
    for i, page in enumerate(reader.pages, start=1):
        chunks.append(f"\n===== PAGE {i} =====\n{page.extract_text() or ''}")

    text = "".join(chunks)
    OUT.write_text(text, encoding="utf-8")
    print(f"pages={len(reader.pages)} chars={len(text)} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
