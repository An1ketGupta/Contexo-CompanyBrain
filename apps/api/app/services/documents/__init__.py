"""Document generation pipeline — template upload through email delivery.

Replaces the template handling that lived in `agents/onboarding_v2`. The design
rules this package exists to enforce:

  1. **The LLM never writes legal text.** It classifies the document, detects
     which spans vary per candidate, and proposes names and types for them.
     Filling a document is deterministic span substitution with no model in the
     loop.

  2. **Formatting survives.** Values are spliced at run level, so only the runs
     overlapping a fill-point are touched and every other run keeps its original
     XML. The pipeline this replaces collapsed each affected paragraph into its
     first run, which silently flattened bold labels, mixed fonts, and inline
     links wherever a field appeared.

  3. **Templates are immutable.** Nothing is ever written back into an uploaded
     file. Fill-points are anchors (`doc_template_slots`) into the original
     bytes, resolved at generation time.

Module map:

    docx_positions   canonical (paragraph_index, offset) addressing — the shared
                     contract between detection and rendering
    constants        vocabularies mirroring the CHECK constraints in 099
    audit            append-only trail over `document_audit_logs`
    text_edit        read a *generated* document as editable lines and write
                     edited lines back — the review loop, not templates (rule 3
                     is about uploaded templates; a generated draft is HR's to
                     correct before it is signed)
"""
