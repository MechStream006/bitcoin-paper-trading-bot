"""Research-derived join of persisted feature rows to execution outcomes.

Read-only. Reads the two offline stores and writes nothing to either.

This is a *view*, not a dataset. It exists so a decision can be inspected whole
-- what was known at the instant, and what happened afterwards -- without
recomputing either half. It is deliberately not promoted to training data:
see `joined.RESEARCH_USE_ONLY`.
"""
