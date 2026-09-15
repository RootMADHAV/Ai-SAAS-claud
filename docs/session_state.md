# Session State

Overwritten at the end of every coding session — reflects the most
recent session only. Cumulative history: `docs/implementation_progress.md`.
Permanent architecture/decisions reference: `PROJECT_STATE.md`.

## Date
2026-09-14

## Last completed task
TD #18 re-investigation: `sqlmap-stdout` normalizer. A fifth
out-of-sequence explicit-instruction session, directly following the
SQLMap adapter session, explicitly instructed to attempt the
normalizer with a "stop and document rather than fabricate" safety
valve made explicit up front.

**Outcome: zero files modified (except `PROJECT_STATE.md`).** The
instruction was to parse only what the repository/tests establish as a
reliable SQLMap stdout format, and to stop and document if the
available output is insufficient for safe structured findings, rather
than fabricate semantics. It is insufficient — re-confirmed by fresh
investigation this session, not assumed from the prior session's note.

**What was checked this session:** `normalization.py` (both existing
normalizers, to confirm the established pattern — real, documented,
stable machine-readable formats in both cases: nuclei's `-jsonl`,
nmap's `-oX` XML), `SqlmapAdapter` and its module docstring,
`test_sqlmap_adapter.py`, and then — the part that made this a genuine
re-investigation rather than a repeat — a fresh search of the entire
`tests/` directory tree (`directory_tree`, both `unit/` and
`integration/`) for any real captured sqlmap output, fixture, or
sample file that might serve as a ground-truth reference. None exists.
The *only* sqlmap output content anywhere in this repository is
`test_sqlmap_adapter.py`'s own `_SAMPLE_OUTPUT` constant — and it is
explicitly commented, in the repository's own words, as "a plausible
stand-in for sqlmap's own stdout... not a byte-perfect reproduction of
real sqlmap output." The repository itself documents that nothing
reliable exists to parse.

**Why no parser was written anyway:** sqlmap's real, well-known stdout
format (the "Parameter: X (METHOD)" block structure) is something I
have reasonable general confidence in from training knowledge, but the
instruction specifically scoped what counts as usable evidence to what
the *repository* establishes, not what I recall from outside
knowledge — and building a parser from memory, however confident, is
exactly the "invent an undocumented contract" the instruction ruled
out, just relocated from the code to my own recollection instead of
the codebase. Treating my own unverified memory as a reliable source
would have quietly reintroduced the fabrication risk the instruction
was written to prevent.

## Files changed
None in `backend/`. This was an investigation-only session by design —
the instruction's own explicit branch for "insufficient evidence" was
followed, not worked around. `normalization.py`, `SqlmapAdapter`,
`test_sqlmap_adapter.py`, `test_normalization.py` all remain exactly as
the prior (SQLMap adapter) session left them. The only file touched
this session is `PROJECT_STATE.md` — TD #18 (§12) rewritten to record
precisely what this session checked (not just re-stated the prior
session's note), plus a new §7 session-table row and a new §16
out-of-sequence-session paragraph, both following the established
pattern from the prior Nmap/SQLMap sessions.

## Verification — exact results
Since nothing in `backend/` changed, this was a confirmation pass, not
a build — but run for real rather than cited from the prior session's
numbers, to catch any silent drift:

- **`pytest tests/ -q` → 99 passed, 0 failed** — identical to the
  SQLMap adapter session's own result, as expected with zero code
  changes.
- Ruff (lint + format) and MyPy `--strict`: both still clean on
  `normalization.py`, `app/scanner_engine/adapters/sqlmap/adapter.py`,
  `tests/unit/test_sqlmap_adapter.py`, `tests/unit/test_normalization.py`.

## Pending work
TD #18 remains open, now on its second confirmed investigation rather
than a single unexamined note. Resolving it needs one of: (a) a real,
sqlmap-documented structured output mode being identified (none is
currently known to exist for injection-detection findings specifically
— sqlmap's `--dump-format` options are for exfiltrated table data, a
different concept); (b) real captured sqlmap output being added to this
repository as a genuine fixture, which a parser could then be built and
verified against — the missing ingredient this session identified
precisely, not vaguely; or (c) an explicit human decision to accept the
risk of a deliberately conservative, fragility-flagged text parser
built from outside knowledge, made consciously rather than by an AI
session quietly substituting its own memory for the repository's own
standard of evidence.

SQLMap's own three-session arc (adapter → wiring → normalizer,
mirroring Nmap's) remains one-third complete, unchanged by this
session: no pipeline/API wiring exists yet for `sqlmap`
(`AppState.active_scanners`, `_select_active_scanner`, the
`scanner_name` API literal all still nuclei/nmap-only). Remaining
Phase 4 scanner adapters (burp/zap/reconx/bughunter) untouched. All
Phase 3 frontend items and the standing TD list remain exactly where
prior sessions left them — see PROJECT_STATE.md §12/§13 for the
current, authoritative full list, not re-duplicated here since nothing
about it changed this session.

## Next immediate task
No next task self-selected. TD #18 stays open and honestly documented
rather than closed by fabrication. Independent things still open, none
blocking the others: (1) Step 5's scope for the Phase 3 frontend test
suite; (2) a future SQLMap pipeline/API wiring session (adapter
selection/execution only — would not by itself resolve TD #18); (3)
supplying real captured sqlmap output as a repository fixture, if
Madhav has or can generate one, which would change TD #18's own
calculus directly; (4) whether to continue building further Phase 4
adapters (burp/zap/reconx/bughunter — note burp/zap are
`ImportScanner`-shaped, a genuinely different architecture than every
adapter built so far, and reconx/bughunter have no available spec in
this repo, so either would need its own scoping discussion before
implementation). Await explicit instruction before starting new work.
