# ATR Activation — operator card (2026-07-19)

Companion to `research/forward_evidence/ATR_ACTIVATION_RUNBOOK.md`, which stays
authoritative. This card records what was pre-verified off-host and gives the
exact block to paste on the server. It does not replace the runbook's decision
tree — keep that open while running.

## Already done (repo side, verified locally)

| Runbook gate | Result |
|---|---|
| B1 — edit target unique | `grep -c '^    enabled: false$'` → **1** (line 52) |
| B2 — fn_hash via production guard | **`FNHASH OK: acaf70e9`** ✅ |
| B3 — governance green | `Registry check: OK`, 9 candidate, **exit 0** ✅ |
| Phase D equivalent — dry-run | **`g3_atr_expansion position=-1.0`**, all 5 signals compute, exit 0 ✅ |
| Post-activation checklist: repo file matches server | **done** — repo now `enabled: true` |

**On the Phase D result.** A dry-run at the default `run_date` (yesterday)
returns `unavailable` for **all five** signals on the dev machine, because the
local OHLCV store ends `2026-06-10`. That is dev-corpus staleness, not an ATR
fault — re-running at `--run-date 2026-06-09` (inside local coverage) produced a
clean ATR position. On the server, `daily_ingest.py` keeps OHLCV current, so
Phase D there should use the default date. **If Phase D returns `unavailable`
for ATR on the server, that is a real failure — follow the runbook decision tree
and roll back.**

## Run on the server

```bash
# --- session setup -------------------------------------------------------
KEY=D:/My_paper_trading_bot/bitcoin-key.pem
ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@3.110.103.217

# then, ON THE SERVER:
CFG=/opt/btcbot/research/forward_evidence/collection_config.yaml
PY=/opt/miniconda3/envs/btc_bot/bin/python
EVDB=/opt/btcbot/research/data/evidence_forward.db
STAMP=$(date -u +%Y%m%d)

# --- Phase A: backup (no-clobber) ---------------------------------------
test -f "$CFG.bak.$STAMP" && echo "BACKUP EXISTS — not overwriting" || cp "$CFG" "$CFG.bak.$STAMP"
md5sum "$CFG" "$CFG.bak.$STAMP"          # expect two identical hashes

# --- Phase B: re-verify ON HOST (do not trust the off-host run) ----------
grep -n "enabled:" "$CFG"                 # expect a single: 52:    enabled: false
sudo -u btcbot bash -c "cd /opt/btcbot/research && $PY -c \"from forward_evidence.pre_reg_guard import verify_fn_hash; import pathlib as P; b='forward_evidence/'; print('FNHASH OK:', verify_fn_hash(P.Path(b+'pre_registration_g3_atr_expansion.json'), P.Path(b+'decision_function_g3_atr_expansion.py'), 'g3_atr_expansion')[:8])\""
sudo -u btcbot "$PY" /opt/btcbot/research/evidence_library/report.py --check; echo "exit=$?"

# --- Phase C: the flip ---------------------------------------------------
test "$(grep -c '^    enabled: false$' "$CFG")" = "1" && echo "GUARD OK" || echo "GUARD FAIL — STOP"
sed -i 's/^    enabled: false$/    enabled: true/' "$CFG"
diff "$CFG.bak.$STAMP" "$CFG"             # expect exactly one hunk
grep -n "enabled:" "$CFG"                 # expect 52:    enabled: true

# --- Phase D: dry-run, default run_date ---------------------------------
sudo -u btcbot bash -c "cd /opt/btcbot/research && $PY -m forward_evidence.run_daily --dry-run"; echo "exit=$?"
# expect: [dry-run] g3_atr_expansion ... position=<-1|0|1> (no DB write), exit 0
# 'unavailable' or 'error' => STOP, roll back (Phase F)
```

## Phase E — sign-off (next day)

Canonical sign-off **requires one unattended timer-driven 01:00 UTC cycle**. A
manual `systemctl start` is an in-window smoke test only and is not sufficient.

```bash
journalctl -u btcbot-fwd-hg.service -n 30 --no-pager | tail -20
sudo -u btcbot sqlite3 -header -column "$EVDB" \
 "SELECT signal_id, run_date, status, substr(fn_hash,1,8) fn8 FROM run_log WHERE signal_id='g3_atr_expansion' ORDER BY run_date DESC LIMIT 3;"
sudo -u btcbot sqlite3 -header -column "$EVDB" \
 "SELECT signal_id, MAX(run_date) latest, status FROM run_log GROUP BY signal_id ORDER BY signal_id;"
```

Expect `status='ok'`, `fn8='acaf70e9'`, and **all five** signals at the same
latest `run_date`, all `ok`.

## Rollback

```bash
cp "$CFG.bak.$STAMP" "$CFG"
md5sum "$CFG" "$CFG.bak.$STAMP"   # must match again
grep -n "enabled:" "$CFG"          # back to 'enabled: false'
```

No restart needed — the one-shot re-reads the YAML each run. Run the runbook's
**conditional recovery** `DELETE` **only** if Phase E showed
`fn_hash_violation` or `error`; never purge ATR rows with `status='ok'`.

To also revert the repo: `git checkout -- research/forward_evidence/collection_config.yaml`.

## Ordering hazard with the infrastructure deployment

The repo is now `enabled: true`, which is what the runbook's post-activation
checklist requires (otherwise a redeploy silently reverts ATR). The side effect
is that **a deployment of the repo config will itself activate ATR**, bypassing
the on-host Phase D gate.

So: **run this card before, or as part of, the infrastructure deployment** — not
after. If the deployment ships first, treat that as the activation and run
Phase D/E immediately against the deployed config.

## Operational impact

- Adds a 5th signal to the 01:00 UTC one-shot. Marginal runtime and DB growth;
  `evidence_forward.db` is 36 KB.
- **No** restart, schema change, systemd change, or code change.
- Idempotent on `(signal_id, run_date)` — a repeated or overlapping run
  short-circuits to `skipped` and cannot corrupt state.
- Starts ATR's forward-evidence clock. `promotion_ready` needs
  `min_calendar_months: 6`, so the sooner this runs the sooner ATR is eligible.
- Cannot affect the four existing signals: ATR runs on its own isolated OHLCV
  branch in `run_daily.py`. If H5/G1/G3/IVRV regress, roll back immediately
  (runbook decision tree).
