"""
Layer 2 — Index-Based LCS Diff (The Brain)
============================================
Takes two flat word databases (from Layer 1) and returns two sets of indices:

    removed_indices : set[int]  — indices in OLD database that are gone in NEW
    added_indices   : set[int]  — indices in NEW database that didn't exist in OLD

The algorithm:
  1. Extract just the text strings from both databases → two word lists.
  2. Run InsensitiveSequenceMatcher (LCS-based) to get opcodes.
  3. Walk the opcodes and collect the exact OLD indices for deletions/replacements
     and the exact NEW indices for insertions/replacements.

We never compare indices directly — we compare TEXT, then record which index
in each document was involved. This is exactly "Index-Based LCS."
"""

import difflib
from dataclasses import dataclass
from layer1_extraction import WordObject


# ── Same noise-filter as the original HTML engine ────────────────────────────

class InsensitiveSequenceMatcher(difflib.SequenceMatcher):
    """
    Filters out very small matching islands (< threshold words) to avoid
    creating noisy, fragmented diff regions. A 1-word match in the middle of
    a large changed block usually produces worse output than treating the
    whole block as changed.
    """
    threshold = 2

    def get_matching_blocks(self):
        size = min(len(self.a), len(self.b))
        threshold = min(self.threshold, size / 4)
        actual = super().get_matching_blocks()
        return [m for m in actual if m[2] > threshold or not m[2]]


# ── Diff result ───────────────────────────────────────────────────────────────

@dataclass
class DiffResult:
    """
    The output of the Logic Layer.

    removed_indices : indices into the OLD database that were removed
    added_indices   : indices into the NEW database that were added
    stats           : summary counts
    """
    removed_indices: set
    added_indices:   set
    stats:           dict

    def summary(self) -> str:
        s = self.stats
        return (f"Pages: {s['old_pages']} → {s['new_pages']}  |  "
                f"Words: {s['old_words']} → {s['new_words']}  |  "
                f"Removed: {s['removed']}  Added: {s['added']}  "
                f"Unchanged: {s['unchanged']}  "
                f"Change rate: {s['change_pct']}%")


# ── Core diff function ────────────────────────────────────────────────────────

def compute_diff(
    old_db: list[WordObject],
    new_db: list[WordObject],
) -> DiffResult:
    """
    Run the LCS diff on two word databases.

    Parameters
    ----------
    old_db : list of WordObject
        Word database extracted from the OLD PDF.
    new_db : list of WordObject
        Word database extracted from the NEW PDF.

    Returns
    -------
    DiffResult
        Two sets of target indices — one for each PDF.
    """
    # Extract plain text lists for the sequence matcher
    old_words = [w.text for w in old_db]
    new_words = [w.text for w in new_db]

    matcher = InsensitiveSequenceMatcher(
        a=old_words,
        b=new_words,
        autojunk=False,
    )
    opcodes = matcher.get_opcodes()

    removed_indices: set[int] = set()   # indices into old_db
    added_indices:   set[int] = set()   # indices into new_db
    unchanged_count = 0

    for op, i1, i2, j1, j2 in opcodes:
        if op == "equal":
            unchanged_count += (i2 - i1)

        elif op == "delete":
            # Words old[i1:i2] were removed — record their global indices
            for word_obj in old_db[i1:i2]:
                removed_indices.add(word_obj.index)

        elif op == "insert":
            # Words new[j1:j2] are brand new — record their global indices
            for word_obj in new_db[j1:j2]:
                added_indices.add(word_obj.index)

        elif op == "replace":
            # Old words replaced → removed; new words → added
            for word_obj in old_db[i1:i2]:
                removed_indices.add(word_obj.index)
            for word_obj in new_db[j1:j2]:
                added_indices.add(word_obj.index)

    # Gather page counts
    old_pages = max((w.page_number for w in old_db), default=0)
    new_pages = max((w.page_number for w in new_db), default=0)
    total = len(old_words) + len(new_words)
    change_pct = round(
        100 * (len(removed_indices) + len(added_indices)) / max(total, 1), 1
    )

    stats = {
        "old_words":   len(old_words),
        "new_words":   len(new_words),
        "removed":     len(removed_indices),
        "added":       len(added_indices),
        "unchanged":   unchanged_count,
        "change_pct":  change_pct,
        "old_pages":   old_pages,
        "new_pages":   new_pages,
    }

    return DiffResult(
        removed_indices=removed_indices,
        added_indices=added_indices,
        stats=stats,
    )


if __name__ == "__main__":
    import sys
    from layer1_extraction import extract_word_database

    if len(sys.argv) < 3:
        print("Usage: python layer2_diff.py <old.pdf> <new.pdf>")
        sys.exit(1)

    old_db = extract_word_database(sys.argv[1])
    new_db = extract_word_database(sys.argv[2])
    result = compute_diff(old_db, new_db)

    print(result.summary())
    print(f"\nSample removed indices (first 10): {sorted(result.removed_indices)[:10]}")
    print(f"Sample added   indices (first 10): {sorted(result.added_indices)[:10]}")
