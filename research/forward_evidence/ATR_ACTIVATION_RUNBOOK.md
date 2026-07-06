# Phase 5 — ATR Activation Runbook (CANONICAL)

| Field | Value |
|---|---|
| Version | 1.0 |
| Date | 2026-06-28 |
| Status | Authoritative — no further design changes unless a production issue is discovered |
| Change | `g3_atr_expansion.enabled: false → true` (one line) |
| Production file | `/opt/btcbot/research/forward_evidence/collection_config.yaml` (line 52) |
| Host | `ubuntu@3.110.103.217` |
| Service | `btcbot-fwd-hg.service` (one-shot, 01:00 UTC timer) |
| Restart required | None — one-shot re-reads YAML each run |
| Code / schema / systemd changes | None |
| Reversibility | Full, via file restore; no restart |
| Authorization gate | Clean Phase 4 audit **+** explicit operator go |

> The collection runner is fully idempotent on `(signal_id, run_date)` (`runner_base.py`: idempotency check precedes the fn_hash check; `run_log` uses `INSERT OR IGNORE`). A repeated or overlapping run for the same date short-circuits to `skipped` — it cannot corrupt state or violate constraints.

---

## Session setup

Run on the operator workstation:

```bash
KEY=D:/My_paper_trading_bot/bitcoin-key.pem
ssh -i "$KEY" -o StrictHostKeyChecking=no ubuntu@3.110.103.217
```

Then paste this once **on the server** to set variables for all phases:

```bash
CFG=/opt/btcbot/research/forward_evidence/collection_config.yaml
PY=/opt/miniconda3/envs/btc_bot/bin/python
EVDB=/opt/btcbot/research/data/evidence_forward.db
STAMP=$(date -u +%Y%m%d)
```

---

## Phase A — Backup (no-clobber)

```bash
test -f "$CFG.bak.$STAMP" && echo "BACKUP EXISTS — not overwriting" || cp "$CFG" "$CFG.bak.$STAMP"
md5sum "$CFG" "$CFG.bak.$STAMP"
```

**Expected:** two identical MD5 hashes. If `BACKUP EXISTS` prints, the hashes must still match the original known-good file before proceeding; if they differ, STOP — a prior run may have altered the file.

---

## Phase B — Pre-activation verification

**B1 — edit target is unique:**
```bash
grep -n "enabled:" "$CFG"
```
**Expected:** a single line — `52:    enabled: false`.

**B2 — fn_hash via the production guard (authoritative mechanism):**
```bash
sudo -u btcbot bash -c "cd /opt/btcbot/research && $PY -c \"from forward_evidence.pre_reg_guard import verify_fn_hash; import pathlib as P; b='forward_evidence/'; print('FNHASH OK:', verify_fn_hash(P.Path(b+'pre_registration_g3_atr_expansion.json'), P.Path(b+'decision_function_g3_atr_expansion.py'), 'g3_atr_expansion')[:8])\""
```
**Expected:** `FNHASH OK: acaf70e9`. A `FnHashViolation` traceback ⇒ **STOP** (do not flip; the live guard would also abort).

**B3 — governance is green before the change:**
```bash
sudo -u btcbot "$PY" /opt/btcbot/research/evidence_library/report.py --check; echo "exit=$?"
```
**Expected:** `Registry check: OK … candidate: 9 …` and `exit=0`.

> OHLCV availability is validated by the Phase D dry-run, which uses the production load path at the correct `run_date` and reports `unavailable` if the Parquet partition is missing. No separate OHLCV pre-check is required.

---

## Phase C — Config change

```bash
test "$(grep -c '^    enabled: false$' "$CFG")" = "1" && echo "GUARD OK" || echo "GUARD FAIL — STOP"
sed -i 's/^    enabled: false$/    enabled: true/' "$CFG"
diff "$CFG.bak.$STAMP" "$CFG"
grep -n "enabled:" "$CFG"
```

**Expected:**
- `GUARD OK`
- `diff` shows exactly one hunk: `<     enabled: false` → `>     enabled: true`
- `grep` now shows `52:    enabled: true`

---

## Phase D — Dry-run (no DB write)

```bash
sudo -u btcbot bash -c "cd /opt/btcbot/research && $PY -m forward_evidence.run_daily --dry-run"; echo "exit=$?"
```

**Expected:** `Registry check: OK`; a line `[dry-run] g3_atr_expansion … position=<-1|0|1> (no DB write)`; the other four signals `[dry-run] … ok`; `exit=0`.
- `g3_atr_expansion … unavailable` ⇒ OHLCV partition missing → STOP / rollback (Phase F).
- `error` ⇒ STOP / rollback.

---

## Phase E — Post-activation validation

The **canonical sign-off requires one clean unattended timer-driven 01:00 UTC cycle.** A manual `systemctl start` may be used for an in-window smoke test, but is **not sufficient** for sign-off.

```bash
# OPTIONAL in-window smoke test (processes yesterday's run_date, identical path):
sudo systemctl start btcbot-fwd-hg.service; echo "start exit=$?"

# E1 — journal of the latest run
journalctl -u btcbot-fwd-hg.service -n 30 --no-pager | tail -20

# E2 — signal_log row for ATR
sudo -u btcbot sqlite3 -header -column "$EVDB" \
 "SELECT signal_id, day, position, round(raw_value,5) raw FROM signal_log WHERE signal_id='g3_atr_expansion' ORDER BY day DESC LIMIT 3;"

# E3 — run_log status + fn_hash
sudo -u btcbot sqlite3 -header -column "$EVDB" \
 "SELECT signal_id, run_date, status, substr(fn_hash,1,8) fn8 FROM run_log WHERE signal_id='g3_atr_expansion' ORDER BY run_date DESC LIMIT 3;"

# E4 — all five signals aligned and healthy
sudo -u btcbot sqlite3 -header -column "$EVDB" \
 "SELECT signal_id, MAX(run_date) latest, status FROM run_log GROUP BY signal_id ORDER BY signal_id;"
```

**Expected:**
- E1: `[g3_atr_expansion] ok run_date=<D> position=…`; no `fn_hash_violation`/`error`; service `Finished`, exit 0.
- E2: a fresh `g3_atr_expansion` row at `day=<D>` with `raw` populated.
- E3: `status='ok'`, `fn8='acaf70e9'`.
- E4: all five signals at the same latest `run_date`, all `ok`.

---

## Phase F — Rollback

Reserve for an actual failure trigger (see decision tree). No restart needed — the next run reads `enabled: false` and skips ATR.

```bash
cp "$CFG.bak.$STAMP" "$CFG"
md5sum "$CFG" "$CFG.bak.$STAMP"   # must match again
grep -n "enabled:" "$CFG"          # back to 'enabled: false'
```

**Conditional recovery — run ONLY if E3 showed `fn_hash_violation` or `error`** (these rows are permanent and block same-day retry):

```bash
sudo -u btcbot sqlite3 "$EVDB" \
 "DELETE FROM run_log WHERE signal_id='g3_atr_expansion' AND status IN ('fn_hash_violation','error');"
```

This clears the blocking audit row so a clean retry is possible after the root cause is fixed. The idempotency guard leaves valid `ok` days untouched. Any ATR `ok` rows already written are real append-only observations — do **not** purge them.

---

## Failure scenarios & decision tree

```
Phase B
├─ B2 FnHashViolation traceback   → STOP. Do NOT flip. Investigate decision-function file.
└─ B3 governance exit ≠ 0          → STOP. Do NOT flip. Resolve registry/ledger first.

Phase D (after flip, before live)
├─ g3_atr_expansion = unavailable  → ROLLBACK (Phase F). Fix Parquet ingest; ATR needs daily range.
├─ g3_atr_expansion = error        → ROLLBACK. Investigate before retry.
└─ position printed, exit 0         → CONTINUE to live cycle.

Phase E (unattended timer cycle)
├─ status='ok', fn8='acaf70e9', E4 all ok → SUCCESS. Proceed to post-checklist.
├─ status='fn_hash_violation'             → ROLLBACK + conditional recovery. File integrity issue.
├─ status='error'                         → ROLLBACK + conditional recovery. Capture journal.
├─ status='unavailable' (OHLCV)           → Transient ingest timing. Do NOT rollback yet; re-check
│                                           next cycle. Rollback only if it persists ≥2 cycles.
└─ any of H5/G1/G3/IVRV regressed         → ROLLBACK immediately.
```

---

## Production checklist

**Pre-activation**
- [ ] Phase 4 audit passed, zero Critical/High findings
- [ ] Operator authorization to activate (explicit)
- [ ] Phase A backup taken; MD5 matches known-good
- [ ] B1 single `enabled: false` match
- [ ] B2 `FNHASH OK: acaf70e9`
- [ ] B3 governance exit 0

**During**
- [ ] Phase C guard `GUARD OK`
- [ ] `diff` shows exactly one hunk
- [ ] Phase D dry-run prints ATR position, exit 0 (no `unavailable`/`error`)

**Post-activation**
- [ ] One **unattended timer-driven** 01:00 UTC cycle observed (manual trigger alone is insufficient)
- [ ] E1 journal: `g3_atr_expansion ok`, no violation/error
- [ ] E2 `signal_log` ATR row present
- [ ] E3 `run_log` status ok, fn8 `acaf70e9`
- [ ] E4 all five signals at same `run_date`, all ok
- [ ] **REQUIRED:** commit the identical one-line change to the repo `collection_config.yaml`
      ⚠ WARNING: this change is server-local; a future redeploy from the repo (still `enabled: false`)
      will silently revert ATR to disabled unless the repo is updated to match.

---

## Go / No-Go checklist (< 5 minutes)

Run these; **all must pass** to proceed:

```bash
echo "1) audit:    [manual] Phase 4 audit = PASS, 0 critical?  (y/n)"
echo "2) target:"; grep -c '^    enabled: false$' "$CFG"                                  # expect 1
echo "3) fnhash:";  sudo -u btcbot bash -c "cd /opt/btcbot/research && $PY -c \"from forward_evidence.pre_reg_guard import verify_fn_hash; import pathlib as P; b='forward_evidence/'; verify_fn_hash(P.Path(b+'pre_registration_g3_atr_expansion.json'), P.Path(b+'decision_function_g3_atr_expansion.py'), 'g3_atr_expansion')\"" >/dev/null 2>&1 && echo OK || echo FAIL
echo "4) govern:";  sudo -u btcbot "$PY" /opt/btcbot/research/evidence_library/report.py --check >/dev/null 2>&1 && echo OK || echo FAIL
echo "5) backup:";  test -f "$CFG.bak.$STAMP" && echo OK || echo "TAKE BACKUP FIRST"
```

| # | Gate | GO condition |
|---|---|---|
| 1 | Phase 4 audit | PASS, no critical findings |
| 2 | Edit target unique | output `1` |
| 3 | fn_hash (guard) | `OK` |
| 4 | Governance | `OK` |
| 5 | Backup present | `OK` |

**GO** = all five satisfied → execute Phases C → D → E.
**NO-GO** = any gate fails → stop, do not flip, follow the decision tree.
