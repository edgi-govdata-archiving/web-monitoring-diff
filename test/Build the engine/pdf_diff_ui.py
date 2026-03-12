"""
PDF Diff — Desktop Interface
=============================
A clean, minimal desktop UI to compare two PDF files.
Drop in your OLD and NEW PDFs, click Compare, get a highlighted diff PDF.
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path


# import the engine
try:
    from pdf_diff_engine import build_diff_pdf
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pdf_diff_engine import build_diff_pdf


# pretty colors! 🎨
BG        = "#0F0F1A"
SURFACE   = "#1A1A2E"
SURFACE2  = "#16213E"
ACCENT    = "#4F8EF7"
ADDED     = "#4FC3F7"
REMOVED   = "#FFD54F"
TEXT      = "#E8EAF6"
SUBTEXT   = "#7986CB"
SUCCESS   = "#66BB6A"
ERROR     = "#EF5350"
BORDER    = "#2D3561"


class DropZone(tk.Frame):
    # drag & drop zone for PDFs
    def __init__(self, master, label, color_accent, on_file_selected, **kwargs):
        super().__init__(master, bg=SURFACE, **kwargs)
        self.color_accent = color_accent
        self.on_file_selected = on_file_selected
        self.filepath = None

        self.configure(
            highlightbackground=BORDER,
            highlightthickness=1,
            cursor="hand2"
        )

        # label badge
        self.badge = tk.Label(
            self, text=label,
            bg=color_accent, fg="#0F0F1A",
            font=("Helvetica", 9, "bold"),
            padx=8, pady=2
        )
        self.badge.pack(pady=(14, 4))

        # big document icon 📄
        self.icon_label = tk.Label(
            self, text="📄",
            bg=SURFACE,
            font=("Helvetica", 28)
        )
        self.icon_label.pack()

        # main instruction text
        self.main_label = tk.Label(
            self, text="Click to select PDF",
            bg=SURFACE, fg=SUBTEXT,
            font=("Helvetica", 11)
        )
        self.main_label.pack(pady=(4, 2))

        # shows selected filename
        self.file_label = tk.Label(
            self, text="No file selected",
            bg=SURFACE, fg=SUBTEXT,
            font=("Helvetica", 9),
            wraplength=200
        )
        self.file_label.pack(pady=(0, 14))

        # make everything clickable
        for widget in (self, self.badge, self.icon_label, self.main_label, self.file_label):
            widget.bind("<Button-1>", self._browse)
            widget.bind("<Enter>", self._on_hover)
            widget.bind("<Leave>", self._on_leave)

    def _browse(self):
        # open file picker
        path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
        )
        if path:
            self.set_file(path)

    def set_file(self, path):
        # update UI with selected file
        self.filepath = path
        name = Path(path).name
        display = name if len(name) <= 28 else name[:25] + "…"
        self.file_label.config(text=display, fg=TEXT)
        self.icon_label.config(text="✅")
        self.main_label.config(text="File selected", fg=self.color_accent)
        self.configure(highlightbackground=self.color_accent)
        self.on_file_selected()

    def _on_hover(self):
        # highlight on hover
        self.configure(highlightbackground=self.color_accent, highlightthickness=2)

    def _on_leave(self):
        # reset border on leave
        border = self.color_accent if self.filepath else BORDER
        self.configure(highlightbackground=border, highlightthickness=1)


class PDFDiffApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF Diff")
        self.geometry("580x660")
        self.resizable(False, False)
        self.configure(bg=BG)

        self._build_ui()

    def _build_ui(self):
        # top header bar
        topbar = tk.Frame(self, bg=SURFACE2, height=56)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)

        tk.Label(
            topbar, text="⬛ PDF DIFF",
            bg=SURFACE2, fg=TEXT,
            font=("Courier", 15, "bold")
        ).pack(side="left", padx=20, pady=14)

        tk.Label(
            topbar, text="word-by-word • LCS algorithm",
            bg=SURFACE2, fg=SUBTEXT,
            font=("Helvetica", 9)
        ).pack(side="right", padx=20, pady=18)

        # main content area
        content = tk.Frame(self, bg=BG)
        content.pack(fill="both", expand=True, padx=24, pady=20)

        # subtitle
        tk.Label(
            content,
            text="Compare two PDF versions and get a highlighted diff report.",
            bg=BG, fg=SUBTEXT,
            font=("Helvetica", 10)
        ).pack(anchor="w", pady=(0, 16))

        # side-by-side zones for files
        zones_frame = tk.Frame(content, bg=BG)
        zones_frame.pack(fill="x", pady=(0, 16))

        self.old_zone = DropZone(
            zones_frame,
            label="  OLD VERSION  ",
            color_accent=REMOVED,
            on_file_selected=self._on_file_selected,
            width=240, height=160
        )
        self.old_zone.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.new_zone = DropZone(
            zones_frame,
            label="  NEW VERSION  ",
            color_accent=ADDED,
            on_file_selected=self._on_file_selected,
            width=240, height=160
        )
        self.new_zone.pack(side="right", fill="both", expand=True, padx=(8, 0))

        # arrow decoration
        tk.Label(zones_frame, text="→", bg=BG, fg=SUBTEXT, font=("Helvetica", 22)).pack()
        zones_frame.update_idletasks()

        # output file section
        out_frame = tk.Frame(content, bg=BG)
        out_frame.pack(fill="x", pady=(0, 20))

        tk.Label(
            out_frame, text="Output PDF:",
            bg=BG, fg=TEXT,
            font=("Helvetica", 10)
        ).pack(side="left", padx=(0, 8))

        self.output_var = tk.StringVar(value=str(Path.home() / "pdf_diff_result.pdf"))
        out_entry = tk.Entry(
            out_frame,
            textvariable=self.output_var,
            bg=SURFACE, fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Helvetica", 9),
            width=36
        )
        out_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 6))

        tk.Button(
            out_frame, text="Browse",
            bg=SURFACE2, fg=SUBTEXT,
            activebackground=BORDER, activeforeground=TEXT,
            relief="flat",
            font=("Helvetica", 9),
            cursor="hand2",
            command=self._browse_output,
            padx=8
        ).pack(side="right")

        # color legend
        legend = tk.Frame(content, bg=SURFACE, pady=10)
        legend.pack(fill="x", pady=(0, 16))

        for color, label in [(ADDED, "Added / Modified"), (REMOVED, "Removed")]:
            row = tk.Frame(legend, bg=SURFACE)
            row.pack(side="left", padx=16)
            tk.Label(row, text="  ", bg=color, width=2).pack(side="left", padx=(0, 6))
            tk.Label(row, text=label, bg=SURFACE, fg=TEXT, font=("Helvetica", 9)).pack(side="left")

        # main compare button
        self.compare_btn = tk.Button(
            content,
            text="Compare PDFs →",
            bg=ACCENT, fg="#FFFFFF",
            activebackground="#3D7AE5",
            activeforeground="#FFFFFF",
            relief="flat",
            font=("Helvetica", 12, "bold"),
            cursor="hand2",
            command=self._run_diff,
            padx=20, pady=12,
            state="disabled"
        )
        self.compare_btn.pack(fill="x", pady=(0, 14))

        # progress spinner
        self.progress = ttk.Progressbar(
            content, mode="indeterminate",
            style="TProgressbar"
        )

        # status message
        self.status_var = tk.StringVar(value="Select both PDF files to begin.")
        self.status_label = tk.Label(
            content,
            textvariable=self.status_var,
            bg=BG, fg=SUBTEXT,
            font=("Helvetica", 10),
            wraplength=500,
            justify="left"
        )
        self.status_label.pack(anchor="w")

        # results stats display
        self.stats_frame = tk.Frame(content, bg=SURFACE)

        # style the progressbar
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure(
            "TProgressbar",
            troughcolor=SURFACE,
            background=ACCENT,
            thickness=4
        )

    def _on_file_selected(self):
        # enable compare button when both files selected
        if self.old_zone.filepath and self.new_zone.filepath:
            self.compare_btn.config(state="normal", bg=ACCENT)
            self.status_var.set("Ready. Click 'Compare PDFs' to start.")

    def _browse_output(self):
        # pick where to save the result
        path = filedialog.asksaveasfilename(
            title="Save diff PDF as",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")]
        )
        if path:
            self.output_var.set(path)

    def _run_diff(self):
        # validate and start diff process
        old_path = self.old_zone.filepath
        new_path = self.new_zone.filepath
        out_path = self.output_var.get().strip()

        if not old_path or not new_path:
            messagebox.showwarning("Missing Files", "Please select both PDF files.")
            return
        if not out_path:
            messagebox.showwarning("No Output", "Please specify an output file path.")
            return

        # disable UI during processing
        self.compare_btn.config(state="disabled", text="Processing…")
        self.progress.pack(fill="x", pady=(8, 0))
        self.progress.start(10)
        self.status_var.set("Extracting text and computing diff…")
        self.status_label.config(fg=SUBTEXT)

        # clear old stats
        self.stats_frame.pack_forget()
        for w in self.stats_frame.winfo_children():
            w.destroy()

        def worker():
            # run diff in background thread
            try:
                stats = build_diff_pdf(old_path, new_path, out_path)
                self.after(0, lambda: self._on_success(stats, out_path))
            except Exception as exc:
                self.after(0, lambda: self._on_error(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_success(self, stats, out_path):
        # show results and stats
        self.progress.stop()
        self.progress.pack_forget()
        self.compare_btn.config(state="normal", text="Compare PDFs →")

        self.status_var.set(f"✅ Done! Diff saved to: {out_path}")
        self.status_label.config(fg=SUCCESS)

        # display stats
        self.stats_frame.pack(fill="x", pady=(12, 0))
        items = [
            ("Pages",    str(stats["pages"]),     TEXT),
            ("Added",    f"+{stats['added']}",     ADDED),
            ("Removed",  f"-{stats['removed']}",   REMOVED),
            ("Changed",  f"{stats['pct']}%",        ACCENT),
        ]
        for label, value, color in items:
            col = tk.Frame(self.stats_frame, bg=SURFACE)
            col.pack(side="left", expand=True, fill="both", padx=4, pady=8)
            tk.Label(col, text=value, bg=SURFACE, fg=color,
                     font=("Helvetica", 18, "bold")).pack()
            tk.Label(col, text=label, bg=SURFACE, fg=SUBTEXT,
                     font=("Helvetica", 8)).pack()

        # open file button
        tk.Button(
            self.stats_frame,
            text="Open PDF",
            bg=SUCCESS, fg="#0F0F1A",
            relief="flat",
            font=("Helvetica", 9, "bold"),
            cursor="hand2",
            command=lambda: self._open_file(out_path),
            padx=10, pady=6
        ).pack(side="right", padx=8, pady=8)

    def _on_error(self, error_msg):
        # show error and reset UI
        self.progress.stop()
        self.progress.pack_forget()
        self.compare_btn.config(state="normal", text="Compare PDFs →")
        self.status_var.set(f"❌ Error: {error_msg}")
        self.status_label.config(fg=ERROR)

    def _open_file(self, path):
        # open result PDF with system default app
        import subprocess, platform
        try:
            if platform.system() == "Darwin":
                subprocess.run(["open", path])
            elif platform.system() == "Windows":
                os.startfile(path)
            else:
                subprocess.run(["xdg-open", path])
        except Exception:
            messagebox.showinfo("Open File", f"File saved at:\n{path}")


if __name__ == "__main__":
    app = PDFDiffApp()
    app.mainloop()
