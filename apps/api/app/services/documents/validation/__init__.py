"""Pre-generation validation.

`engine.validate` decides whether there is enough correct data to produce a
document, and `engine.build_context` produces the values that will actually be
spliced in. They live together deliberately: what gets checked and what gets
rendered must be derived from the same rules, or validation is theatre.
"""
