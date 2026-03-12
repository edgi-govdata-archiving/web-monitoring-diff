"""
Layer 1 — Data Extraction
==========================
Scans a PDF with pdfplumber and builds a flat "Word Database":
a list of WordObject namedtuples, each pinned to its exact physical
location on the original page via a BoundingBox.

WordObject fields:
    index        : int   — global zero-based position across all pages
    text         : str   — the word string (stripped)
    page_number  : int   — 1-based page number
    bbox         : BoundingBox(x0, y0, x1, y1) in PDF points
                          (origin = bottom-left of page, as pdfplumber uses)
"""

from dataclasses import dataclass
from typing import NamedTuple
import pdfplumber
import re


class BoundingBox(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class WordObject:
    index:       int
    text:        str
    page_number: int
    bbox:        BoundingBox

    def __repr__(self):
        return (f"WordObject(idx={self.index}, text={self.text!r}, "
                f"page={self.page_number}, bbox={self.bbox})")


def extract_word_database(pdf_path: str) -> list[WordObject]:
    """
    Open a PDF and return a flat list of WordObjects — one per word,
    across all pages, in reading order.

    pdfplumber's `extract_words()` already groups characters into words
    and gives us the bounding box for each group. We just flatten and
    index them.
    """
    database: list[WordObject] = []
    global_index = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            # extract_words() returns dicts with keys:
            #   text, x0, top, x1, bottom  (top/bottom = distance from top of page)
            # We keep all words, including punctuation attached to words.
            words = page.extract_words(
                x_tolerance=3,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=True,
            )

            for word_dict in words:
                raw = word_dict["text"].strip()
                if not raw:
                    continue

                # pdfplumber gives (x0, top, x1, bottom) where top/bottom
                # are measured from the TOP of the page. We store as-is —
                # consistent with pdfplumber's own coordinate system.
                bbox = BoundingBox(
                    x0=float(word_dict["x0"]),
                    y0=float(word_dict["top"]),
                    x1=float(word_dict["x1"]),
                    y1=float(word_dict["bottom"]),
                )

                database.append(WordObject(
                    index=global_index,
                    text=raw,
                    page_number=page_num,
                    bbox=bbox,
                ))
                global_index += 1

    return database


def get_page_dimensions(pdf_path: str) -> dict[int, tuple[float, float]]:
    """
    Return {page_number: (width, height)} for every page.
    Needed by the paint layer to draw highlights correctly.
    """
    dims = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            dims[page_num] = (float(page.width), float(page.height))
    return dims


if __name__ == "__main__":
    import sys, json
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage: python layer1_extraction.py <file.pdf>")
        sys.exit(1)

    db = extract_word_database(path)
    print(f"Extracted {len(db)} words from '{path}'")
    print("\nFirst 10 word objects:")
    for w in db[:10]:
        print(f"  {w}")
