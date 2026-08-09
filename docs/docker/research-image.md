# `Dockerfile.research` — build & validate

Implements ADR-0001 §5.3 (Phase 1: Docker Foundation). Read that ADR first —
this doc is the operational how-to, not the design rationale.

## What this image is, and is not

- **Is:** a CI-reproducibility image for the `research/` corpus-build
  pipeline (ingest, hypothesis runners, evidence signal computation).
- **Is not:** a production artifact. It is never deployed, never referenced
  by `deploy/*.service`, never referenced by Docker Compose (Phase 3+). It
  has no production role.
- **Does not contain:** `.env`, any `*.db`, `research/data/`, or
  `research/evidence_library/` (the governance ledger's home). This is
  enforced by the repo-root `.dockerignore`, not by convention — verified
  empirically below, not merely asserted.
- **Does not contain:** `src/` or `config/`. Nothing under `research/` has a
  module-level import from either (verified by grep before this image was
  built), so the research and runtime planes stay dependency-isolated inside
  the image too, matching ADR-0001 B3.

## Build

From the repo root (the build context must be the repo root so
`.dockerignore` applies):

```
docker build -f Dockerfile.research -t btcbot-research:local .
```

Expect roughly 60-90s cold (numpy/scipy/pandas/pyarrow/statsmodels wheels are
the bulk of it), then near-instant on a warm layer cache since the lockfile
layer is copied and installed before the rest of `research/`.

## Validate before trusting a build

Run all four. All four must pass before this image is used for anything
(including just eyeballing a corpus-build script's output).

**1. Runs as non-root:**

```
docker run --rm btcbot-research:local id
# expect: uid=10001(researcher) gid=10001(researcher)
```

**2. No project data, no secrets (INV-1):**

```
docker run --rm btcbot-research:local sh -c \
  'find /app \( -iname "*.db" -o -iname ".env" -o -path "*/evidence_library*" -o -path "*/research/data/*" \)'
# expect: no output
```

**3. Python version matches the validated research pinset:**

```
docker run --rm btcbot-research:local python --version
# expect: Python 3.10.20  (CLAUDE.md §4: "pinset validated on 3.10.20")
```

**4. Self-contained research test suite passes inside the container:**

```
docker run --rm btcbot-research:local sh -c \
  'cd /app && python -m pytest research/forward_evidence/tests/ -q'
# expect: all tests passed (182 at time of writing)
```

This test suite was chosen deliberately: it lives entirely under
`research/forward_evidence/`, imports nothing from `evidence_library/` or
`src/`, and needs no fixture data outside its own `tmp_path` usage — so it
exercises real code inside the real image without requiring anything the
image deliberately excludes.

All four checks above are also run automatically on every push/PR via the
`docker-research-image` job in `.github/workflows/ci.yml` (Phase 1's "build
verification" deliverable) — this doc is for a developer validating a local
build before that CI run, or for re-verifying by hand after a change to the
Dockerfile or `.dockerignore`.

## Reproducibility

- Base image pinned by **digest**, not tag (ADR-0001 §5.1/§5.4). The tag
  comment in `Dockerfile.research` is for humans; the digest is what's
  actually pulled, so a Docker Hub retag of `python:3.10.20-slim-bookworm`
  cannot silently change this build.
- All Python dependencies come from `research/requirements-research-lock.txt`
  — the full `pip freeze` lockfile, not the human-readable direct-dependency
  list — so the dependency graph is exactly pinned, not just top-level
  versions.
- What reproducibility means here in practice: the same dependency versions
  and the same Python patch version, on any machine or CI runner, every
  time. It does **not** mean byte-identical image digests across rebuilds
  (that needs additional BuildKit determinism flags this project doesn't
  currently use, and nothing in ADR-0001 asks for it).

### Refreshing the pinned base-image digest

Only do this deliberately, not as a side effect of an unrelated change:

```
docker pull python:3.10.20-slim-bookworm
docker inspect python:3.10.20-slim-bookworm --format='{{index .RepoDigests 0}}'
```

Paste the resulting digest into the `FROM` line and update the digest
comment above it. Re-run all four validation checks and the CI job.

## Known security findings (as of 2026-07-18, `docker scout cves`)

Started at **15 vulnerabilities in 6 packages** (1 CRITICAL, 14 HIGH). After
the fix below, **13 in 4 packages** (1 CRITICAL, 12 HIGH) — verified by an
actual before/after rescan, not assumed. Recorded here rather than silently
fixed further, because the remaining findings require a decision Phase 1
(Docker Foundation) shouldn't make unilaterally:

| Finding | Source | Action taken |
|---|---|---|
| Outdated vendored `wheel==0.45.1` (CVE-2026-24049) and `jaraco-context==5.3.0` (CVE-2026-23949) copies inside `setuptools/_vendor/` (2× HIGH) | setuptools 79.0.1, bundled by the base image | **Fixed in this image.** `RUN pip install --upgrade pip setuptools` in `Dockerfile.research` (setuptools 79.0.1 → 83.0.0) refreshes its vendored bundle. Confirmed by `find / -iname "*wheel*"` / `*jaraco*` inside the image before and after: the vulnerable vendored copies are gone; only current versions remain. Zero behavioral risk — setuptools's vendored copies aren't imported by any research code. |
| `pillow==12.2.0` (9× HIGH, fixed in 12.3.0), `urllib3==2.6.3` (2× HIGH, fixed in 2.7.0), `cryptography==48.0.0` (1× HIGH, GHSA-537c-gmf6-5ccf, fixed in 48.0.1) | transitive deps in `research/requirements-research-lock.txt`, pinned 2026-06-10 | **Not fixed here.** All three are patch-level bumps, but `requirements-research-lock.txt` is shared with the non-Docker `btc_bot` conda environment used for real research work — editing it is a dependency-pinning decision with a blast radius beyond "Docker Foundation," so it's surfaced as a recommended fast-follow rather than changed silently in this PR. |
| `CVE-2026-12087` in Debian's `perl` package (CRITICAL, no upstream fix yet) | `python:3.10.20-slim-bookworm` base image OS layer | **Not fixable today** — Debian has not shipped a patch. Inherited base-image risk, not something introduced by this Dockerfile. Materially low exploitability here: this image runs as an ephemeral, non-root, no-listening-port batch container invoked explicitly for one script at a time — it is never a long-running network-facing service. Re-check with `docker scout cves` after any future base-digest refresh; no action until Debian ships a fix. |

Re-scan anytime with:

```
docker scout cves btcbot-research:local --only-severity critical,high
```
