"""RFP Agent — autonomous RFP response pipeline.

Re-entrant state machine that walks an `rfp_responses` row from a fresh
upload through extraction, rep review, drafting, optional legal review,
and finally a format-preserving export back into the buyer's original file.

Key modules:

  parser.py    — Table-aware XLSX/DOCX/PDF parsing. Detects question column
                 and answer column on XLSX so the exporter can fill the
                 buyer's original sheet rather than emitting a fresh doc.

  extractor.py — Normalizes parser output and falls back to LLM for free-text
                 RFPs. Also runs semantic dedup (clustering near-duplicate
                 questions so the answerer runs once per cluster).

  answerer.py  — Per-requirement multi-tool LLM loop. The LLM may issue up
                 to 3 search_company_knowledge calls per requirement (synonym
                 coverage), scoped to the org's "RFP-approved" collection.

  exporter.py  — Format-preserving export. XLSX → fills original Answer
                 column with openpyxl, preserving formatting. DOCX/PDF →
                 always emits a clean DOCX summary with gap callouts. XLSX
                 always also gets a summary DOCX.

  storage.py   — Supabase Storage I/O for source bytes + outputs. Tempfiles
                 don't survive worker restarts so we don't use them.

  agent.py     — RfpAgent(BaseAgent). The state-machine dispatcher.
"""
from __future__ import annotations

from .agent import RfpAgent

__all__ = ["RfpAgent"]
