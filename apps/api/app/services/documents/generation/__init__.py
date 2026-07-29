"""Deterministic document generation.

    docx_splice   run-level text surgery — the piece that makes "preserve
                  formatting" true rather than aspirational
    renderer      drift-guarded fill + PDF conversion

No LLM is reachable from anything in this package. Generation is pure
substitution at recorded positions, which is the only way a legal document's
wording can be guaranteed unchanged.
"""
