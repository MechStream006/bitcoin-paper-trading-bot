"""Canonical timestamp alignment across the project's data sources.

Read-only. Nothing here writes to any store.

The layer exists to answer one question consistently: *at a given decision
instant, what did we actually know?* Every source in this project carries at
least two distinct times -- when the event happened, and when this system could
first have acted on it -- and they are not the same. Aligning on the former
produces features that look prescient and are not reproducible live.

`timebase` declares, per source, which field is which. `align` does the
as-of joining and refuses joins that would reach forward.
"""
