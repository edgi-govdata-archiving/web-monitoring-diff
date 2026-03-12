"""
PDF Diff Engine: Compares two PDFs word-by-word using LCS.
Produces a report with blue (added), yellow (removed), and normal text.
"""

import difflib
import re
import os
import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, HRFlowable, Table, TableStyle

# Colors for diff visualization
COLOR_ADDED = colors.HexColor('#C8E6FF')
COLOR_REMOVED = colors.HexColor('#FFF59D')
COLOR_ADDED_BORDER = colors.HexColor('#1A6FBF')
COLOR_REMOVED_BORDER = colors.HexColor('#B8860B')
COLOR_PAGE_BG = colors.HexColor('#F8F9FA')
COLOR_HEADING = colors.HexColor('#1A1A2E')
COLOR_SUBTEXT = colors.HexColor('#6B7280')
COLOR_DIVIDER = colors.HexColor('#E5E7EB')


def extract_pages(pdf_path: str) -> list[dict]:
    """Extract text from each page of a PDF."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            pages.append({"page": i, "text": text})
    return pages


def tokenize(text: str) -> list[str]:
    """Split text into words (remove whitespace)."""
    return re.findall(r'\S+', text)


class InsensitiveSequenceMatcher(difflib.SequenceMatcher):
    """Filter out tiny matches to avoid fragmented diffs."""
    threshold = 2

    def get_matching_blocks(self):
        size = min(len(self.a), len(self.b))
        threshold = min(self.threshold, size / 4)
        actual = super().get_matching_blocks()
        return [m for m in actual if m[2] > threshold or not m[2]]


def compute_diff(old_words: list[str], new_words: list[str]) -> list[tuple]:
    """Run LCS diff on word lists. Return opcodes."""
    matcher = InsensitiveSequenceMatcher(a=old_words, b=new_words, autojunk=False)
    return matcher.get_opcodes()


def count_changes(opcodes, old_words, new_words) -> tuple:
    """Count added, removed, and unchanged words."""
    added = removed = unchanged = 0
    for op, i1, i2, j1, j2 in opcodes:
        if op == 'equal':
            unchanged += (i2 - i1)
        elif op == 'insert':
            added += (j2 - j1)
        elif op == 'delete':
            removed += (i2 - i1)
        elif op == 'replace':
            added += (j2 - j1)
            removed += (i2 - i1)
    return added, removed, unchanged


def _escape(text: str) -> str:
    """Escape XML special chars for ReportLab."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def build_diff_paragraph(opcodes, old_words, new_words, style) -> Paragraph:
    """Build a paragraph with colored diff markup."""
    parts = []
    for op, i1, i2, j1, j2 in opcodes:
        if op == 'equal':
            for word in old_words[i1:i2]:
                parts.append(_escape(word) + ' ')
        elif op == 'insert':
            for word in new_words[j1:j2]:
                parts.append(f'<font backColor="#C8E6FF"><b>{_escape(word)}</b></font> ')
        elif op == 'delete':
            for word in old_words[i1:i2]:
                parts.append(f'<font backColor="#FFF59D"><strike>{_escape(word)}</strike></font> ')
        elif op == 'replace':
            for word in old_words[i1:i2]:
                parts.append(f'<font backColor="#FFF59D"><strike>{_escape(word)}</strike></font> ')
            for word in new_words[j1:j2]:
                parts.append(f'<font backColor="#C8E6FF"><b>{_escape(word)}</b></font> ')

    markup = ''.join(parts).strip() or '<font color="#AAAAAA"><i>(empty)</i></font>'
    return Paragraph(markup, style)


def build_diff_pdf(old_path: str, new_path: str, output_path: str) -> dict:
    """Generate a diff PDF report comparing two PDFs."""
    print(f"  Extracting: {old_path}")
    old_pages = extract_pages(old_path)
    print(f"  Extracting: {new_path}")
    new_pages = extract_pages(new_path)

    # Setup document
    doc = SimpleDocTemplate(output_path, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm,
                           topMargin=18*mm, bottomMargin=18*mm, title="PDF Diff Report")
    styles = getSampleStyleSheet()

    # Define custom styles
    title_style = ParagraphStyle('DiffTitle', parent=styles['Normal'], fontSize=22, leading=28,
                                 textColor=COLOR_HEADING, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('DiffSubtitle', parent=styles['Normal'], fontSize=10,
                                    textColor=COLOR_SUBTEXT)
    page_label_style = ParagraphStyle('PageLabel', parent=styles['Normal'], fontSize=9,
                                      textColor=COLOR_SUBTEXT, fontName='Helvetica-Bold')
    content_style = ParagraphStyle('DiffContent', parent=styles['Normal'], fontSize=10, leading=16)
    stat_style = ParagraphStyle('StatStyle', parent=styles['Normal'], fontSize=10)

    story = []
    total_added = total_removed = total_unchanged = 0

    # Cover section
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph("PDF Diff Report", title_style))
    story.append(Paragraph("Word-by-word comparison using LCS", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_DIVIDER, spaceAfter=6))

    # File info table
    file_data = [
        [Paragraph('<b>Version</b>', stat_style), Paragraph('<b>File</b>', stat_style),
         Paragraph('<b>Pages</b>', stat_style)],
        [Paragraph('OLD', stat_style), Paragraph(_escape(os.path.basename(old_path)), stat_style),
         Paragraph(str(len(old_pages)), stat_style)],
        [Paragraph('NEW', stat_style), Paragraph(_escape(os.path.basename(new_path)), stat_style),
         Paragraph(str(len(new_pages)), stat_style)],
    ]
    file_table = Table(file_data, colWidths=[25*mm, 110*mm, 25*mm])
    file_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), COLOR_DIVIDER),
        ('BACKGROUND', (0, 1), (0, 1), COLOR_REMOVED),
        ('BACKGROUND', (0, 2), (0, 2), COLOR_ADDED),
        ('GRID', (0, 0), (-1, -1), 0.5, COLOR_DIVIDER),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(file_table)
    story.append(Spacer(1, 5*mm))

    # Legend
    legend_data = [[
        Paragraph('<font backColor="#C8E6FF"><b>Added</b></font> words in blue', stat_style),
        Paragraph('<font backColor="#FFF59D"><strike>Removed</strike></font> words in yellow', stat_style),
    ]]
    legend_table = Table(legend_data, colWidths=[83*mm, 83*mm])
    legend_table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, COLOR_DIVIDER),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(legend_table)
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_DIVIDER, spaceAfter=4))

    # Per-page diff
    num_pages = max(len(old_pages), len(new_pages))
    for idx in range(num_pages):
        old_text = old_pages[idx]["text"] if idx < len(old_pages) else ""
        new_text = new_pages[idx]["text"] if idx < len(new_pages) else ""
        page_num = idx + 1

        old_words = tokenize(old_text)
        new_words = tokenize(new_text)
        opcodes = compute_diff(old_words, new_words)
        added, removed, unchanged = count_changes(opcodes, old_words, new_words)
        total_added += added
        total_removed += removed
        total_unchanged += unchanged

        # Page header
        story.append(Paragraph(
            f"PAGE {page_num}   <font color='#1A6FBF'>+{added}</font>   "
            f"<font color='#B8860B'>-{removed}</font>   <font color='#6B7280'>{unchanged}</font>",
            page_label_style))
        story.append(build_diff_paragraph(opcodes, old_words, new_words, content_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_DIVIDER, spaceAfter=4))

    # Summary page
    story.append(PageBreak())
    story.append(Paragraph("Summary", title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_DIVIDER, spaceAfter=8))

    total_words = total_added + total_removed + total_unchanged
    pct_changed = round(100 * (total_added + total_removed) / max(total_words, 1), 1)

    summary_data = [
        ['Metric', 'Count'],
        ['Pages', str(num_pages)],
        ['Added', str(total_added)],
        ['Removed', str(total_removed)],
        ['Unchanged', str(total_unchanged)],
        ['Change Rate', f"{pct_changed}%"],
    ]
    summary_table = Table(summary_data, colWidths=[80*mm, 60*mm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), COLOR_HEADING),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 2), (-1, 2), COLOR_ADDED),
        ('BACKGROUND', (0, 3), (-1, 3), COLOR_REMOVED),
        ('GRID', (0, 0), (-1, -1), 0.5, COLOR_DIVIDER),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(summary_table)
    doc.build(story)

    return {"pages": num_pages, "added": total_added, "removed": total_removed,
            "unchanged": total_unchanged, "pct": pct_changed}


if __name__ == "__main__":
    result = build_diff_pdf("old.pdf", "new.pdf", "diff_report.pdf")
    print("Report generated:", result)
