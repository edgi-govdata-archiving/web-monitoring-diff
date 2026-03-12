"""
Layer 4 — Synchronized Dual-Pane Viewport (The Presentation Layer)
====================================================================
A Tkinter desktop application that:

  1. Accepts two PDF file paths (old and new).
  2. Runs Layers 1-3 in a background thread.
  3. Opens both marked PDFs side-by-side.
  4. Synchronizes scrolling: scrolling LEFT moves RIGHT proportionally
     using the index bridge — because we know which OLD index corresponds
     to which NEW index.

Layout:
  ┌─────────────────────────────────────────────────────┐
  │  TOP BAR: file names + status + stats               │
  ├────────────────────┬────────────────────────────────┤
  │  OLD (yellow)      │  NEW (blue)                    │
  │  left pane         │  right pane                    │
  │                    │                                │
  ├────────────────────┴────────────────────────────────┤
  │  BOTTOM BAR: page indicator + legend                │
  └─────────────────────────────────────────────────────┘
"""

import os
import sys
import math
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path
import tempfile

import pypdfium2 as pdfium
from PIL import Image, ImageTk


# ── Import our layers ─────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from layer1_extraction import extract_word_database
from layer2_diff        import compute_diff
from layer3_paint       import paint_both


# ── Color palette ─────────────────────────────────────────────────────────────
BG          = "#0D1117"
SURFACE     = "#161B22"
SURFACE2    = "#21262D"
BORDER      = "#30363D"
TEXT        = "#E6EDF3"
SUBTEXT     = "#8B949E"
ACCENT      = "#58A6FF"
REMOVED_COL = "#E3B341"   # amber
ADDED_COL   = "#58A6FF"   # blue
SUCCESS     = "#3FB950"
ERROR       = "#F85149"
PANE_BG     = "#010409"


# ── Render scale for the viewer (lower than Layer 3 for speed) ───────────────
VIEW_SCALE  = 1.5


class PDFPane(tk.Frame):
    """
    A single scrollable PDF viewer pane. Renders pages from a PDF file
    into a Tkinter Canvas using pypdfium2.
    """

    def __init__(self, master, label: str, label_color: str, **kwargs):
        super().__init__(master, bg=BG, **kwargs)
        self.label_color = label_color
        self.pdf_path    = None
        self._pdf_doc    = None
        self._page_imgs  = []        # list of PIL Images (one per page)
        self._tk_imgs    = []        # list of ImageTk.PhotoImage (kept alive)
        self._page_tops  = []        # y-pixel position where each page starts
        self._total_height = 0
        self._canvas_width = 0
        self._sync_callback = None   # called with (fraction) on scroll

        # Header label
        header = tk.Frame(self, bg=SURFACE2, height=32)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text=f"  {label}",
            bg=SURFACE2, fg=label_color,
            font=("Courier", 10, "bold")
        ).pack(side="left", padx=8, pady=6)

        self.page_label = tk.Label(
            header, text="",
            bg=SURFACE2, fg=SUBTEXT,
            font=("Helvetica", 9)
        )
        self.page_label.pack(side="right", padx=10)

        # Canvas + scrollbar
        frame = tk.Frame(self, bg=PANE_BG)
        frame.pack(fill="both", expand=True)

        self.vbar = tk.Scrollbar(frame, orient="vertical", bg=SURFACE2,
                                 troughcolor=SURFACE, activebackground=ACCENT)
        self.vbar.pack(side="right", fill="y")

        self.canvas = tk.Canvas(
            frame,
            bg=PANE_BG,
            highlightthickness=0,
            yscrollcommand=self._on_scroll,
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.config(command=self._canvas_yview)

        self.canvas.bind("<Configure>",    self._on_resize)
        self.canvas.bind("<MouseWheel>",   self._on_mousewheel)
        self.canvas.bind("<Button-4>",     self._on_mousewheel)
        self.canvas.bind("<Button-5>",     self._on_mousewheel)

    # ── Loading ───────────────────────────────────────────────────────────────

    def load_pdf(self, pdf_path: str):
        """Load and render all pages of the PDF into memory."""
        self.pdf_path = pdf_path
        if self._pdf_doc:
            self._pdf_doc.close()

        self._pdf_doc   = pdfium.PdfDocument(pdf_path)
        self._page_imgs = []

        for page in self._pdf_doc:
            bitmap = page.render(scale=VIEW_SCALE)
            img    = bitmap.to_pil().convert("RGB")
            self._page_imgs.append(img)
            page.close()

        self._layout_pages()

    def _layout_pages(self):
        """Calculate page positions and draw everything on the canvas."""
        if not self._page_imgs:
            return

        self.canvas.update_idletasks()
        canvas_w = max(self.canvas.winfo_width(), 400)
        self._canvas_width = canvas_w

        GAP = 12   # pixels between pages
        y   = GAP
        self._page_tops = []
        self._tk_imgs   = []

        self.canvas.delete("all")

        for img in self._page_imgs:
            # Scale image to fit canvas width
            scale    = canvas_w / img.width
            new_w    = canvas_w
            new_h    = int(img.height * scale)
            resized  = img.resize((new_w, new_h), Image.LANCZOS)
            tk_img   = ImageTk.PhotoImage(resized)
            self._tk_imgs.append(tk_img)
            self._page_tops.append(y)
            self.canvas.create_image(0, y, anchor="nw", image=tk_img)
            y += new_h + GAP

        self._total_height = y
        self.canvas.config(scrollregion=(0, 0, canvas_w, self._total_height))

    def _on_resize(self, event):
        if self._page_imgs:
            self._layout_pages()

    # ── Scrolling ─────────────────────────────────────────────────────────────

    def _canvas_yview(self, *args):
        self.canvas.yview(*args)

    def _on_scroll(self, lo, hi):
        """Called when canvas scrolls. Update scrollbar and notify sync."""
        self.vbar.set(lo, hi)
        frac = float(lo)
        if self._sync_callback:
            self._sync_callback(frac)
        # Update page indicator
        self._update_page_label()

    def _on_mousewheel(self, event):
        if event.num == 4 or event.delta > 0:
            self.canvas.yview_scroll(-2, "units")
        else:
            self.canvas.yview_scroll(2, "units")

    def set_sync_callback(self, cb):
        self._sync_callback = cb

    def scroll_to_fraction(self, frac: float):
        """Programmatically scroll to a fractional position [0.0 … 1.0]."""
        self.canvas.yview_moveto(frac)

    def get_scroll_fraction(self) -> float:
        return float(self.canvas.yview()[0])

    def _update_page_label(self):
        if not self._page_tops:
            return
        # Find which page is currently at the top of the viewport
        view_top = self.get_scroll_fraction() * self._total_height
        page_num = 1
        for i, top in enumerate(self._page_tops):
            if top <= view_top:
                page_num = i + 1
        total = len(self._page_tops)
        self.page_label.config(text=f"Page {page_num} / {total}")


# ── Index Bridge for scroll sync ──────────────────────────────────────────────

class IndexBridge:
    """
    Maps a scroll fraction on the OLD side to the equivalent fraction on
    the NEW side, using the diff opcodes as the bridge.

    Idea: both PDFs share a common "backbone" of equal (unchanged) words.
    We build a lookup: for a given position in old_db, what is the
    corresponding position in new_db?
    """

    def __init__(self, opcodes, old_db, new_db):
        # Build a sorted list of (old_word_idx, new_word_idx) anchor pairs
        # from all 'equal' spans in the opcodes.
        self._old_count = len(old_db)
        self._new_count = len(new_db)
        self._anchors   = []          # [(old_frac, new_frac), ...]

        for op, i1, i2, j1, j2 in opcodes:
            if op == "equal" and i2 > i1:
                # middle of this equal span
                mid_old = (i1 + i2) / 2
                mid_new = (j1 + j2) / 2
                old_frac = mid_old / max(self._old_count, 1)
                new_frac = mid_new / max(self._new_count, 1)
                self._anchors.append((old_frac, new_frac))

        # Always include 0→0 and 1→1 as boundary anchors
        self._anchors = [(0.0, 0.0)] + self._anchors + [(1.0, 1.0)]

    def old_to_new(self, old_frac: float) -> float:
        """Given a scroll fraction in the OLD pane, return the equiv for NEW."""
        anchors = self._anchors
        # Find the two surrounding anchors and linearly interpolate
        for i in range(len(anchors) - 1):
            a_old, a_new = anchors[i]
            b_old, b_new = anchors[i + 1]
            if a_old <= old_frac <= b_old:
                if b_old == a_old:
                    return a_new
                t = (old_frac - a_old) / (b_old - a_old)
                return a_new + t * (b_new - a_new)
        return old_frac   # fallback: 1:1

    def new_to_old(self, new_frac: float) -> float:
        """Given a scroll fraction in the NEW pane, return the equiv for OLD."""
        anchors = self._anchors
        for i in range(len(anchors) - 1):
            a_old, a_new = anchors[i]
            b_old, b_new = anchors[i + 1]
            if a_new <= new_frac <= b_new:
                if b_new == a_new:
                    return a_old
                t = (new_frac - a_new) / (b_new - a_new)
                return a_old + t * (b_old - a_old)
        return new_frac


# ── Main Application ──────────────────────────────────────────────────────────

class PDFDiffApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("PDF Diff — Dual Pane Viewer")
        self.geometry("1280x820")
        self.minsize(900, 600)
        self.configure(bg=BG)

        self._bridge      = None
        self._syncing     = False       # prevent recursive scroll callbacks
        self._tmp_dir     = tempfile.mkdtemp(prefix="pdfdiff_")
        self._old_path    = None
        self._new_path    = None

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Top bar ───────────────────────────────────────────────────────────
        topbar = tk.Frame(self, bg=SURFACE, height=52)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        tk.Label(
            topbar, text="  PDF DIFF",
            bg=SURFACE, fg=TEXT,
            font=("Courier", 13, "bold")
        ).pack(side="left", pady=14)

        # File picker buttons
        btn_style = dict(bg=SURFACE2, fg=SUBTEXT, relief="flat",
                         font=("Helvetica", 9), cursor="hand2",
                         activebackground=BORDER, activeforeground=TEXT,
                         padx=10, pady=4)

        self.old_btn = tk.Button(topbar, text="📂 Old PDF", **btn_style,
                                 command=lambda: self._pick_file("old"))
        self.old_btn.pack(side="left", padx=(20, 4), pady=12)

        self.new_btn = tk.Button(topbar, text="📂 New PDF", **btn_style,
                                 command=lambda: self._pick_file("new"))
        self.new_btn.pack(side="left", padx=4, pady=12)

        self.run_btn = tk.Button(
            topbar, text="▶  Run Diff",
            bg=ACCENT, fg="#0D1117",
            relief="flat", font=("Helvetica", 10, "bold"),
            cursor="hand2",
            activebackground="#79B8FF",
            padx=14, pady=4,
            command=self._run_diff,
            state="disabled"
        )
        self.run_btn.pack(side="left", padx=(12, 0), pady=12)

        self.status_var = tk.StringVar(value="Select Old and New PDF files to begin.")
        tk.Label(
            topbar, textvariable=self.status_var,
            bg=SURFACE, fg=SUBTEXT,
            font=("Helvetica", 9)
        ).pack(side="left", padx=16)

        # Stats on the right
        self.stats_var = tk.StringVar(value="")
        tk.Label(
            topbar, textvariable=self.stats_var,
            bg=SURFACE, fg=SUBTEXT,
            font=("Helvetica", 9)
        ).pack(side="right", padx=16)

        # ── Progress bar (hidden until needed) ───────────────────────────────
        self.progress = ttk.Progressbar(self, mode="indeterminate", length=200)
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("TProgressbar", troughcolor=SURFACE,
                        background=ACCENT, thickness=3)

        # ── Dual panes ────────────────────────────────────────────────────────
        panes = tk.Frame(self, bg=BORDER)
        panes.pack(fill="both", expand=True)

        self.left_pane = PDFPane(
            panes,
            label="OLD VERSION",
            label_color=REMOVED_COL,
        )
        self.left_pane.pack(side="left", fill="both", expand=True)

        tk.Frame(panes, bg=BORDER, width=2).pack(side="left", fill="y")

        self.right_pane = PDFPane(
            panes,
            label="NEW VERSION",
            label_color=ADDED_COL,
        )
        self.right_pane.pack(side="right", fill="both", expand=True)

        # Wire up scroll sync
        self.left_pane.set_sync_callback(self._left_scrolled)
        self.right_pane.set_sync_callback(self._right_scrolled)

        # ── Bottom legend bar ─────────────────────────────────────────────────
        bottom = tk.Frame(self, bg=SURFACE2, height=32)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)

        for color_hex, label in [(REMOVED_COL, "Removed (old)"), (ADDED_COL, "Added (new)")]:
            row = tk.Frame(bottom, bg=SURFACE2)
            row.pack(side="left", padx=16, pady=8)
            tk.Label(row, text="  ", bg=color_hex, width=2).pack(side="left", padx=(0, 5))
            tk.Label(row, text=label, bg=SURFACE2, fg=TEXT,
                     font=("Helvetica", 9)).pack(side="left")

        tk.Label(
            bottom, text="Scroll either pane — both sides sync automatically",
            bg=SURFACE2, fg=SUBTEXT, font=("Helvetica", 8)
        ).pack(side="right", padx=16)

    # ── File selection ────────────────────────────────────────────────────────

    def _pick_file(self, side: str):
        path = filedialog.askopenfilename(
            title=f"Select {'Old' if side == 'old' else 'New'} PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
        )
        if not path:
            return

        if side == "old":
            self._old_path = path
            name = Path(path).name
            self.old_btn.config(text=f"📄 {name[:20]}…" if len(name) > 20 else f"📄 {name}",
                                fg=REMOVED_COL)
        else:
            self._new_path = path
            name = Path(path).name
            self.new_btn.config(text=f"📄 {name[:20]}…" if len(name) > 20 else f"📄 {name}",
                                fg=ADDED_COL)

        if self._old_path and self._new_path:
            self.run_btn.config(state="normal")
            self.status_var.set("Ready. Click ▶ Run Diff.")

    # ── Run diff pipeline ─────────────────────────────────────────────────────

    def _run_diff(self):
        self.run_btn.config(state="disabled")
        self.progress.pack(fill="x")
        self.progress.start(10)
        self.status_var.set("Extracting words…")
        self.stats_var.set("")

        def pipeline():
            try:
                # Layer 1
                self.after(0, lambda: self.status_var.set("Layer 1 — Building word database…"))
                old_db = extract_word_database(self._old_path)
                new_db = extract_word_database(self._new_path)

                # Layer 2
                self.after(0, lambda: self.status_var.set("Layer 2 — Running LCS diff…"))
                from layer2_diff import compute_diff, InsensitiveSequenceMatcher
                import difflib

                old_words = [w.text for w in old_db]
                new_words = [w.text for w in new_db]
                matcher   = InsensitiveSequenceMatcher(a=old_words, b=new_words, autojunk=False)
                opcodes   = matcher.get_opcodes()

                diff_result = compute_diff(old_db, new_db)

                # Layer 3
                self.after(0, lambda: self.status_var.set("Layer 3 — Painting highlights…"))
                old_marked, new_marked = paint_both(
                    self._old_path, self._new_path,
                    old_db, new_db,
                    diff_result.removed_indices,
                    diff_result.added_indices,
                    output_dir=self._tmp_dir,
                )

                # Build index bridge for scroll sync
                bridge = IndexBridge(opcodes, old_db, new_db)

                self.after(0, lambda: self._show_results(
                    old_marked, new_marked, bridge, diff_result.stats
                ))

            except Exception as exc:
                import traceback
                tb = traceback.format_exc()
                self.after(0, lambda: self._show_error(str(exc), tb))

        threading.Thread(target=pipeline, daemon=True).start()

    def _show_results(self, old_marked, new_marked, bridge, stats):
        self.progress.stop()
        self.progress.pack_forget()
        self.run_btn.config(state="normal")
        self._bridge = bridge

        self.status_var.set("✅ Diff complete — scroll either pane.")
        self.stats_var.set(
            f"Removed: {stats['removed']}  │  Added: {stats['added']}  │  "
            f"Unchanged: {stats['unchanged']}  │  Changed: {stats['change_pct']}%"
        )

        self.status_var.set("Loading PDFs into viewer…")
        self.left_pane.load_pdf(old_marked)
        self.right_pane.load_pdf(new_marked)
        self.status_var.set("✅ Done — scroll either pane to sync both sides.")

    def _show_error(self, msg, tb):
        self.progress.stop()
        self.progress.pack_forget()
        self.run_btn.config(state="normal")
        self.status_var.set(f"❌ {msg}")
        print("ERROR:\n", tb)

    # ── Scroll sync ───────────────────────────────────────────────────────────

    def _left_scrolled(self, frac: float):
        if self._syncing or not self._bridge:
            return
        self._syncing = True
        mapped = self._bridge.old_to_new(frac)
        self.right_pane.scroll_to_fraction(mapped)
        self._syncing = False

    def _right_scrolled(self, frac: float):
        if self._syncing or not self._bridge:
            return
        self._syncing = True
        mapped = self._bridge.new_to_old(frac)
        self.left_pane.scroll_to_fraction(mapped)
        self._syncing = False


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = PDFDiffApp()
    app.mainloop()
