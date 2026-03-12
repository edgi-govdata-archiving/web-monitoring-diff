"""
Layer 3 — Direct Injection (The Painter)
==========================================
Takes the target index sets from Layer 2 and paints highlights
directly onto copies of the original PDFs.

  OLD PDF  →  Light Yellow highlights on removed words  →  old_marked.pdf
  NEW PDF  →  Light Blue   highlights on added words    →  new_marked.pdf

Strategy:
  - Render each page to a high-res image using pypdfium2
  - Draw colored rectangles over the bounding boxes from our word database
  - Re-assemble the annotated images back into a PDF using Pillow/reportlab

We render to images rather than trying to inject annotation objects into
the PDF stream because annotation support across PDF libraries is fragile
and coordinate-system transforms are error-prone. Image rendering gives us
pixel-perfect results that match exactly what the user sees.
"""

import io
import os
from pathlib import Path
from typing import Literal

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as rl_canvas

from layer1_extraction import WordObject, get_page_dimensions


# ── Highlight colors (RGBA) ───────────────────────────────────────────────────
COLOR_REMOVED = (255, 235,  59, 120)   # Yellow — removed from OLD
COLOR_ADDED   = ( 66, 165, 245, 120)   # Blue   — added in NEW


# ── Render scale: 2× gives sharp text without huge file sizes ─────────────────
RENDER_SCALE = 2.0


def _group_by_page(
    word_db: list[WordObject],
    target_indices: set[int],
) -> dict[int, list[WordObject]]:
    """
    Filter the word database to only target words and group them by page.
    Returns {page_number: [WordObject, ...]}
    """
    grouped: dict[int, list[WordObject]] = {}
    for word in word_db:
        if word.index in target_indices:
            grouped.setdefault(word.page_number, []).append(word)
    return grouped


def _paint_page(
    pdfium_page,
    highlight_words: list[WordObject],
    color: tuple,
    page_height_pts: float,
) -> Image.Image:
    """
    Render one PDF page to a PIL image, then draw highlight rectangles
    over the specified words.

    pdfplumber uses a top-left origin (y increases downward).
    pypdfium2 renders with the same orientation.
    So we can map pdfplumber bbox coords → pixel coords directly.
    """
    # Render the page to a bitmap at RENDER_SCALE
    bitmap = pdfium_page.render(scale=RENDER_SCALE)
    img = bitmap.to_pil().convert("RGBA")
    img_width, img_height = img.size

    # Create a transparent overlay for highlights
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # pdfplumber's coordinate origin is top-left, y increases downward.
    # pypdfium2 renders top-left origin too at this scale.
    # Scale factor: pixels per PDF point
    pts_width  = img_width  / RENDER_SCALE
    pts_height = img_height / RENDER_SCALE

    scale_x = img_width  / pts_width
    scale_y = img_height / pts_height

    for word in highlight_words:
        # pdfplumber: x0, top(=y0), x1, bottom(=y1) — top-left origin
        px0 = int(word.bbox.x0 * scale_x)
        py0 = int(word.bbox.y0 * scale_y)
        px1 = int(word.bbox.x1 * scale_x)
        py1 = int(word.bbox.y1 * scale_y)

        # Add a small padding so the highlight is slightly larger than the text
        pad = max(1, int(1.5 * RENDER_SCALE))
        px0 = max(0, px0 - pad)
        py0 = max(0, py0 - pad)
        px1 = min(img_width,  px1 + pad)
        py1 = min(img_height, py1 + pad)

        draw.rectangle([px0, py0, px1, py1], fill=color)

    # Composite overlay onto the page image
    img = Image.alpha_composite(img, overlay)
    return img.convert("RGB")


def paint_pdf(
    original_pdf_path: str,
    word_db: list[WordObject],
    target_indices: set[int],
    output_path: str,
    highlight_color: tuple,
    label: str = "",
) -> str:
    """
    Open the original PDF, draw highlights on the relevant pages,
    and write a new PDF to output_path.

    Parameters
    ----------
    original_pdf_path : path to the source PDF
    word_db           : word database for this PDF (from Layer 1)
    target_indices    : set of word indices to highlight
    output_path       : where to write the annotated PDF
    highlight_color   : RGBA tuple for the highlight rectangles
    label             : "OLD" or "NEW" for logging

    Returns
    -------
    str : output_path
    """
    print(f"  [{label}] Painting {len(target_indices)} highlights → {output_path}")

    highlight_map = _group_by_page(word_db, target_indices)
    page_dims     = get_page_dimensions(original_pdf_path)
    num_pages     = len(page_dims)

    pdf_doc = pdfium.PdfDocument(original_pdf_path)

    page_images: list[Image.Image] = []

    for page_num in range(1, num_pages + 1):
        pdfium_page  = pdf_doc[page_num - 1]
        page_h_pts   = page_dims[page_num][1]
        words_on_page = highlight_map.get(page_num, [])

        img = _paint_page(pdfium_page, words_on_page, highlight_color, page_h_pts)
        page_images.append(img)
        pdfium_page.close()

    pdf_doc.close()

    # Save all pages as a multi-page PDF via Pillow
    if page_images:
        first = page_images[0]
        rest  = page_images[1:]
        first.save(
            output_path,
            save_all=True,
            append_images=rest,
            format="PDF",
            resolution=72 * RENDER_SCALE,
        )

    return output_path


def paint_both(
    old_pdf_path:     str,
    new_pdf_path:     str,
    old_db:           list[WordObject],
    new_db:           list[WordObject],
    removed_indices:  set[int],
    added_indices:    set[int],
    output_dir:       str = ".",
) -> tuple[str, str]:
    """
    Convenience wrapper: paint OLD and NEW PDFs and return both output paths.

    Returns
    -------
    (old_marked_path, new_marked_path)
    """
    os.makedirs(output_dir, exist_ok=True)

    old_out = str(Path(output_dir) / "old_marked.pdf")
    new_out = str(Path(output_dir) / "new_marked.pdf")

    paint_pdf(
        original_pdf_path=old_pdf_path,
        word_db=old_db,
        target_indices=removed_indices,
        output_path=old_out,
        highlight_color=COLOR_REMOVED,
        label="OLD",
    )

    paint_pdf(
        original_pdf_path=new_pdf_path,
        word_db=new_db,
        target_indices=added_indices,
        output_path=new_out,
        highlight_color=COLOR_ADDED,
        label="NEW",
    )

    return old_out, new_out


if __name__ == "__main__":
    import sys
    from layer1_extraction import extract_word_database
    from layer2_diff import compute_diff

    if len(sys.argv) < 3:
        print("Usage: python layer3_paint.py <old.pdf> <new.pdf> [output_dir]")
        sys.exit(1)

    old_path = sys.argv[1]
    new_path = sys.argv[2]
    out_dir  = sys.argv[3] if len(sys.argv) > 3 else "."

    print("Extracting word databases…")
    old_db = extract_word_database(old_path)
    new_db = extract_word_database(new_path)

    print("Computing diff…")
    result = compute_diff(old_db, new_db)
    print(result.summary())

    print("Painting highlights…")
    old_out, new_out = paint_both(
        old_path, new_path,
        old_db, new_db,
        result.removed_indices,
        result.added_indices,
        out_dir,
    )

    print(f"\nDone!\n  OLD → {old_out}\n  NEW → {new_out}")
