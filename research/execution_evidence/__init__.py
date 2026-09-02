"""Offline research layer over the execution stack's own record.

Distinct from `forward_evidence`, which analyses the *signal* corpus in
`evidence_forward.db`. This package reads `btc_trading.db` -- the orders,
fills, positions and protective stops the live executor actually produced --
and turns them into a canonical completed-trade dataset plus execution-quality
metrics.

Read-only. Nothing here writes to any database, and nothing imports from
`src/`: the research tree is deliberately decoupled from the live runtime, so
every constant this package needs is either read from the data (contract value
comes from the `instruments` row) or supplied by the caller.
"""
