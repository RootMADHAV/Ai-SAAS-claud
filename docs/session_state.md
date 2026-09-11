# Session State

Overwritten at the end of every coding session — reflects the most
recent session only. Cumulative history: `docs/implementation_progress.md`.
Permanent architecture/decisions reference: `PROJECT_STATE.md`.

## Date
2026-09-10

## Last completed task
Phase 4 — Nmap scanner adapter. An out-of-sequence session run on
explicit direct instruction (not the Step 5 frontend-tests decision
`PROJECT_STATE.md` §16 had flagged as the standing next step) — scoped
tightly to exactly one adapter, with an explicit stop-after-Nmap
instruction honored: no `ScannerPort` redesign, no pipeline changes, no
adapter registry, no new generic abstractions, and no other adapter
(ZAP/Burp/SQLMap/ReconX/BugHunter) touched.

`app/scanner_engine/adapters/nmap/adapter.py` — `NmapAdapter(ActiveScanner)`,
built to the exact same shape as `NucleiAdapter`:
- Routes through the existing `validate_target` and
  `run_scanner_subprocess` — no new subprocess/security-boundary code.
- Returns the existing `ScanOutput` shape (`scanner_name="nmap"`,
  `output_format="nmap-xml"`) — no second pipeline, no new domain type.
- Scan type pinned to `-sT -Pn` (TCP connect + skip host discovery),
  never `-sS`/`-sU`/`-O`/`--traceroute` — the "no privileged/raw-packet
  scanning" requirement made concrete in code, layered on top of (not
  instead of) `run_scanner_subprocess`'s existing non-root guard.
- Target is always the single already-validated, already-resolved
  hostname `validate_target` returns, appended last in argv — never a
  CIDR range or host list, so no target-expansion surface exists
  through this adapter regardless of what a caller passes to
  `execute()`.
- Handles, explicitly and distinctly: missing binary (`FileNotFoundError`
  from `asyncio.create_subprocess_exec`, which `run_scanner_subprocess`
  does not itself catch — caught here and re-raised as
  `ScannerExecutionError`, not a raw `OSError`); timeout (propagates
  `ScannerTimeoutError` from `run_scanner_subprocess` uncaught, same as
  every other failure mode that module already owns); non-zero exit
  (always a hard failure for nmap — deliberately stricter than
  `NucleiAdapter`'s tolerant "non-zero + output present = still a
  success" classification, since nmap has no equivalent benign-warning
  case; documented in the adapter's own comment, not just here); empty
  output; and malformed/non-XML output (a shallow `<nmaprun` substring
  sanity check — real XML parsing stays normalization's job, per
  `PROJECT_STATE.md` §1's Scanning/Findings boundary, matching how
  `NucleiAdapter` also stops at raw bytes).

## Files changed
New: `backend/app/scanner_engine/adapters/nmap/adapter.py`,
`backend/tests/unit/test_nmap_adapter.py`.
Modified (docstring-only, no logic): `backend/app/scanner_engine/adapters/nmap/__init__.py`
(stub → implementation pointer), `backend/app/scanner_engine/adapters/__init__.py`
and `backend/app/scanner_engine/__init__.py` (adapter-count/roster
accuracy — "only one adapter exists" → "two adapters exist"), `PROJECT_STATE.md`
(§5 folder structure, §7 milestone table, §11 testing state, §12 new TD
#15, §13 deferred-work count, §16 next-step note).

## Verification — exact results
Same reconstruct-in-sandbox approach as every prior backend session (no
command-execution tool exists against the real repository — §15):
`NmapAdapter` plus its four direct dependencies (`ScannerPort`/
`ScanOutput`, `validate_target`, `run_scanner_subprocess`, `utcnow`)
were reconstructed verbatim in Claude's sandbox, alongside the
**unmodified** `NucleiAdapter` and `base_scanner` test files, specifically
to check for regressions on code this session did not touch.

- **`pytest tests/unit/ -q` → 20 passed, 0 failed.** 9 new
  `test_nmap_adapter.py` cases (argv construction with/without
  `extra_args`, missing binary, timeout propagation, non-zero exit,
  empty output, malformed output, target-validated-before-subprocess-call,
  name/output_format) + 6 `test_base_scanner.py` + 5
  `test_nuclei_adapter.py` cases — the latter two confirming zero
  regression on the shared code `NmapAdapter` builds on.
- Ruff (lint + format): clean on `adapter.py` and `test_nmap_adapter.py`
  (one round-trip: 8 `E501` lines in the initial test draft from an
  un-wrapped `monkeypatch.setattr(...)` call, fixed, then a `ruff
  format` pass reflowed one `raise` in `adapter.py` — both fixed
  in-session, not left for later, per standing rule).
- MyPy `--strict`: clean, 0 issues, on both new files and the full
  reconstructed `app/` slice (17 source files).
- **Byte-count integrity check** (`get_file_info` on the real repo vs.
  sandbox `wc -c`) confirmed an exact-match transplant for both new
  files: `adapter.py` — 5061 bytes both sides; `test_nmap_adapter.py` —
  7183 bytes both sides.
- **Not verified:** a real `nmap` binary (same documented gap as
  `NucleiAdapter`, TD #7 — now also TD #15). No integration-level test
  was added — per instruction ("only if it fits the existing
  architecture"), and nothing in the existing integration suite
  exercises scanner adapters directly; they're exercised indirectly via
  `RunScanWorkflowUseCase`, which this session did not touch and did
  not need to reconstruct.
- Confirmed by direct diff review: no API-layer file, no pipeline file,
  no other adapter, and no `ScannerPort` file were changed. The
  `scans.py` route's `scanner_name: Literal["nuclei"]` is untouched —
  `NmapAdapter` exists and is tested but is not yet wired into anything
  reachable over HTTP; that wiring was explicitly out of this session's
  scope, not an oversight.

## Pending work
Unchanged from the prior session's own list, plus the Nmap-adapter
follow-ups this session's own scope excluded: wiring `NmapAdapter` into
`scans.py`'s `scanner_name` literal / `RunScanWorkflowUseCase` (not
requested — Phase 4 adapters are being built ahead of pipeline wiring,
by design, matching how `NucleiAdapter` itself was built in Milestone 3
before Milestone 4 wired it in); a real-`nmap`-binary integration test
(TD #15, mirrors TD #7); the remaining 5 Phase 4 stub adapters (burp,
zap, reconx, bughunter, sqlmap — explicitly not started, stop-after-Nmap
was an explicit instruction, not a partial session). All Phase 3
frontend items are untouched by this session and remain exactly where
the prior session left them: Step 5 (tests) awaiting a scope decision,
TD #13 (Next.js 16 evaluation), TD #14 (`frontend/package-lock.json`
still not committed), TD #5/#8/#11/#12, the `E501` finding in
`api/dependencies.py`, Findings/Assets/Reporting HTTP surface, the eight
`docs/*.md` files, `CORS_ALLOWED_ORIGINS` not yet in
`docker-compose.yml`, RBAC/OAuth/MFA/password reset/email
verification/CSRF double-submit/full organization management.

## Next immediate task
No next task self-selected — this session was explicitly scoped to stop
after the Nmap adapter, and did. Two independent open decisions now sit
side by side, neither implied by the other: (1) Step 5's scope for the
Phase 3 frontend test suite (PROJECT_STATE.md §16, unaffected by this
session), and (2) whether/when to wire `NmapAdapter` into the pipeline
and API layer, or instead continue building further Phase 4 stub
adapters (burp/zap/reconx/bughunter/sqlmap) to the same
implement-first-wire-later pattern this session and Milestone 3 both
followed. Await explicit instruction on either before starting new work.
