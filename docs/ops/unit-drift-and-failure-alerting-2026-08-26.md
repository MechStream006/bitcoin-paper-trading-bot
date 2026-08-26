# Unit Drift Reconciliation and Forward-Evidence Failure Alerting — 2026-08-26

**Host:** `3.110.103.217` · **Repo:** `bitcoin-paper-trading-bot` · **Branch:** `main`
**Deployed:** `69ff194` → `d202d7c` · **Gate:** 5C.28
**Closes the detection gap in:** [`INC-0005`](../engineering/incidents/INC-0005-forward-evidence-collection-outage.md)

---

## 1. Host/repository unit drift — discovered before deployment

The deployment target was an `OnFailure=` line added to
`deploy/btcbot-fwd-hg.service`. Pre-deployment comparison of the **deployed**
unit against the **repository** copy found a pre-existing, unrelated difference.

| | `ReadWritePaths` |
|---|---|
| **Host** (`/etc/systemd/system/btcbot-fwd-hg.service`, edited 2026-06-27 21:53:39) | `/opt/btcbot/data /opt/btcbot/research/data /opt/btcbot/research/evidence_library` |
| **Repository** (`deploy/btcbot-fwd-hg.service`, before this change) | `/opt/btcbot/data /opt/btcbot/research/data` |

**Installing the repository copy blindly would have silently removed a write
grant from the running job** — a sandbox tightening that nobody decided, applied
as a side effect of an unrelated change, to the exact service that had already
failed silently for 19 days.

### Evidence gathered on whether the grant is still needed

- `run_daily` reaches `evidence_library/` only by invoking `report.py --check` as
  a subprocess. That entry point is documented as read-only and operates under
  ADR-P1-1 ("assert, never repair").
- `ledger.db` mtime was **2026-08-07 02:54** (the migration) while the last
  successful run was **2026-08-25 01:00** — **18 consecutive daily runs left the
  file untouched**, with no `-wal`/`-shm` sidecars present.

### Resolution: the grant was preserved, and the repository was reconciled to it

The evidence shows the deployed job **does not write** to `evidence_library/`.
It does **not** show that the grant is unnecessary: SQLite may still require
write access to *open* a database, and `ProtectSystem=strict` denies the open,
not merely the write.

**The grant was therefore deliberately preserved and the repository copy
corrected to match the deployed state.** This is explicitly *not* a finding that
the path was proven necessary, and equally not a finding that it was proven
unnecessary — **necessity could not be safely disproven**, and the failure mode
of guessing wrong is precisely the `INC-0005` class: a silent, fail-closed
refusal on a daily one-shot job. Sandboxing was not weakened; the deployed
posture is unchanged, and is now recorded in version control with its rationale.

After reconciliation, a diff of **active directives** (comments excluded)
between the repository and host units differed by exactly one line:
`OnFailure=btcbot-alert@%n.service`.

Committed as `d2b0193`, with a regression test asserting the repository unit
retains all three paths.

---

## 2. Deployment

| SHA | Change |
|---|---|
| `b3e2d97` | `OnFailure=` on `btcbot-fwd-hg.service`; new `scripts/alert_unit_failure.py` and `deploy/btcbot-alert@.service` |
| `d2b0193` | Unit-drift reconciliation (§1) |
| `d202d7c` | `SupplementaryGroups=systemd-journal` on the notifier |

`d202d7c` fixed a defect found during verification, not in production: the
notifier ran as `btcbot`, which was not in `systemd-journal`, so `journalctl`
returned *"No journal files were opened due to insufficient permissions."* The
alert would have reported **that** a unit failed but not **why** — for the
`INC-0005` outage it would have omitted the `IndexError` entirely, defeating the
design goal. Verified afterwards under the real sandbox via a transient
`systemd-run` unit; the dry-run returned a full journal tail, exit 0, no alert
sent and no failure induced.

**No collector restart was required** — only systemd units and one script
changed; `btcbot.service` was untouched and its PID and `NRestarts=0` were
unchanged across the deployment.

---

## 3. Operational invariant now in force

`btcbot-fwd-hg.service` is covered by **two independent detectors**:

| Detector | Catches | Mechanism |
|---|---|---|
| `OnFailure=btcbot-alert@%n.service` | The unit **ran and failed** | systemd escalates a non-zero exit; the notifier posts unit status plus the last 15 journal lines to Telegram |
| `btcbot-healthcheck.timer` (`forward_cycle`) | The unit **did not run at all**, or ran but produced stale data | Independent freshness check on the forward corpus, 10-minute cadence |

The second exists because `OnFailure=` is structurally blind to a job that never
starts. Neither detector can substitute for the other.

### Explicit non-changes

- **No retry behaviour was introduced.** `Restart=` is deliberately absent.
  `run_daily` aborts fail-closed on a governance pre-check failure, and that
  refusal is correct; re-running it would either fail identically or write
  evidence against a registry already judged invalid.
- **No backfill behaviour was introduced.** Nothing in this change reconstructs,
  imputes or fills a missing `run_date`. The `INC-0005` gap remains untouched.
- **`Persistent=true` is unchanged** on `btcbot-fwd-hg.timer`, as is
  `OnCalendar=*-*-* 01:00:00 UTC`. G3 cadence was not altered.
- **The notifier cannot fail its own subject.** It exits 0 unconditionally and
  holds no `ReadWritePaths` under `ProtectSystem=strict`; reading the journal
  mutates nothing.

---

## 4. Lesson

`deploy/` is **not** verified against the host by anything automatic. This drift
was pre-existing, undated in any record, and found only because the unit was
compared by hand before installation.

**Compare the deployed unit against the repository copy before installing any
unit file, and treat an unexplained host-side directive as load-bearing until
proven otherwise.**
