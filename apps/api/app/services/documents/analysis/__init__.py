"""Document understanding — classify the document, find what varies, name it.

Two stages, deliberately separated by what can hallucinate:

    detector.py   Deterministic and local. Finds every fill-point by exact
                  character offset. No LLM, so nothing here can invent a
                  position that does not exist.

    analyzer.py   The LLM stage. Classifies the document type and proposes a
                  name, data type, and confidence for each pre-located
                  fill-point. It also proposes unlabelled literal values, which
                  are then located by exact string search — the model names a
                  substring, never an offset.

The invariant across both: **the model never writes document text and never
authors a position.** Offsets are always computed by `str.find` against the real
paragraph. That is what makes it safe to run an LLM anywhere near a legal
document.
"""
