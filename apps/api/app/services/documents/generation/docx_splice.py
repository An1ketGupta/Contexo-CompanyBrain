"""Run-level text splicing for DOCX paragraphs.

This module exists because of one specific defect in the pipeline it replaces.
The old direct-fill renderer wrote a filled paragraph like this
(`pdf/slot_renderer.py:99-114`):

    runs = paragraph.runs
    runs[0].text = new_text
    for r in runs[1:]:
        r.text = ""

Word splits styled text across runs, so `Annual Salary: ₹12,00,000` is at least
two runs — a bold label and a regular value. Collapsing them into `runs[0]`
gives the *entire* paragraph the first run's formatting. Every paragraph
containing a field came out with its label styling smeared across the value:
bold bled into the amount, mixed fonts flattened, superscripts and inline links
in that paragraph destroyed. The old module's own docstring conceded the point
("collapses to the first run's style"), and the flat-text editor and legacy
mapper made the same trade. It is a direct violation of "preserve formatting",
which is a non-negotiable for legal documents.

What this does instead
----------------------
`splice_paragraph` edits **only the runs that overlap a fill-point**. A run that
does not intersect any edit is never assigned to, so its `<w:rPr>` and its XML
are bit-identical afterwards. Within an edited run, only the `<w:t>` text
changes; the run properties stay attached.

Formatting of an inserted value is inherited from the run containing the START
of the span — the natural reading of "put the value here". When a span crosses
several differently-styled runs, the value necessarily takes one style; that is
inherent to substituting one value for a range, not a shortcut.

Coordinates are offsets into `paragraph_run_text()` — the concatenation of
`paragraph.runs`, which is also what detection indexes into. Hyperlink text is
excluded from that concatenation by `docx_positions`, so hyperlinks are never
addressed and never touched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpliceEdit:
    """Replace `[start, end)` with `value`.

    `start == end` is an insertion point rather than a range, which is how the
    `insert_after_label` and `insert_empty_cell` actions are expressed.
    """

    start: int
    end: int
    value: str


def _run_spans(runs: list[Any]) -> list[tuple[int, int]]:
    """Offset range each run occupies in the concatenated run text."""
    spans: list[tuple[int, int]] = []
    pos = 0
    for run in runs:
        length = len(run.text or "")
        spans.append((pos, pos + length))
        pos += length
    return spans


def _owner_run(
    spans: list[tuple[int, int]], start: int
) -> int:
    """Index of the run whose formatting an inserted value inherits.

    The run *containing* `start`. Zero-length runs cannot contain anything, so
    they are skipped. An offset at the very end of the paragraph belongs to the
    last non-empty run — that is the `insert_after_label` case, where the value
    is appended to the label and should look like the label's tail.
    """
    for i, (s, e) in enumerate(spans):
        if s <= start < e:
            return i

    # At or past the end: attach to the last run that has text, falling back to
    # the last run of all when every run is empty (a blank table cell).
    for i in range(len(spans) - 1, -1, -1):
        s, e = spans[i]
        if e > s:
            return i
    return len(spans) - 1


def splice_paragraph(paragraph: Any, edits: list[SpliceEdit]) -> bool:
    """Apply `edits` to one paragraph, preserving every untouched run.

    `edits` must not overlap; the caller is responsible for that (detection
    guarantees it, and the unique index on the slot anchor enforces it in the
    database). Returns True when the paragraph was modified.

    Implementation note: this computes each run's final text in a single
    left-to-right pass rather than mutating incrementally. Mutating as it went
    would invalidate the very offsets it still needed, which is the kind of bug
    that produces a *plausible* wrong document — the worst possible failure for
    a contract.
    """
    if not edits:
        return False

    runs = list(paragraph.runs)

    if not runs:
        # No runs to inherit from — a genuinely empty paragraph, e.g. a blank
        # table cell. Add one; python-docx gives it the paragraph's style.
        text = "".join(e.value for e in sorted(edits, key=lambda e: e.start))
        if text:
            paragraph.add_run(text)
            return True
        return False

    spans = _run_spans(runs)
    ordered = sorted(edits, key=lambda e: (e.start, e.end))
    owners = [_owner_run(spans, e.start) for e in ordered]

    changed = False
    for i, run in enumerate(runs):
        run_start, _run_end = spans[i]
        original = run.text or ""
        pieces: list[str] = []
        cursor = 0  # position within `original` already emitted

        for edit_index, edit in enumerate(ordered):
            local_start = edit.start - run_start
            local_end = edit.end - run_start
            is_owner = owners[edit_index] == i

            # No interaction with this run at all.
            if local_end < 0 or local_start > len(original):
                continue
            # Touches only the boundary, and belongs to a different run.
            if not is_owner and (local_start >= len(original) or local_end <= 0):
                continue

            clipped_start = max(0, min(local_start, len(original)))
            if clipped_start > cursor:
                pieces.append(original[cursor:clipped_start])
                cursor = clipped_start

            if is_owner:
                pieces.append(edit.value)

            clipped_end = max(0, min(local_end, len(original)))
            if clipped_end > cursor:
                cursor = clipped_end

        pieces.append(original[cursor:])
        new_text = "".join(pieces)
        if new_text != original:
            run.text = new_text
            changed = True

    return changed
