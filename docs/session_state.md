# Session State

Overwritten each session -- latest session only (handoff). Current project
state: `PROJECT_STATE.md`. Compact history: `docs/implementation_progress.md`.

## Date
2026-10-02 (second Phase 6 session)

## Workflow rule (set by the owner this session)
All Phase 6 work is done and verified in the Claude sandbox only. The real
repository is NOT touched until every Phase 6 milestone is complete and
verified; then the final state is transferred and committed in one step.

## Last completed task
1. **Re-verified Phase 6 M1 in the sandbox** (no M1 code changed):
   - Policy evaluated from the real function: owner/admin/member -> read yes,
     create/run yes; viewer -> read yes, create/run no (403).
   - Live app introspection: `POST /scans` and `POST /scans/{id}/run` carry
     `require_scan_write_access` (on top of `require_organization_member`);
     `GET /scans/{id}` carries only `require_organization_member`.
   - Full sandbox suite **52 passed, 0 skipped** (21 unit + 31 integration,
     PostgreSQL 16, non-superuser `app_user`, RLS enforced); Ruff, format and
     MyPy strict clean on the M1 files (70 files); pre-existing `E501` untouched.
2. **Wrote a new compact `PROJECT_STATE.md`** in the sandbox: 22,606 bytes vs
   131,540 for the prior version (~17%, an ~83% reduction). Cross-checked
   against the prior file's content: no required information was lost; two
   small details (frontend session-restore mechanism, runtime dependency list
   incl. `celery[redis]`) were restored after the check.
3. Corrected one unsupported line in the sandbox `implementation_progress.md`:
   it said TD #2, #3, #4 were resolved, but `PROJECT_STATE.md` never records
   them (its debt list jumps 1 -> 5). Now: resolved = #9, #10, #16; #2-#4
   status not recorded.

## Where each document lives right now
| Document | Sandbox | Real repo (untouched this session) |
|---|---|---|
| `PROJECT_STATE.md` | NEW compact, 22,606 B | OLD long version, 131,540 B (already has the M1 edits made before the sandbox-only rule) |
| `old historic project state.md` | **does not exist** | does not exist yet |
| `docs/implementation_progress.md` | 15,039 B (with the TD fix) | 14,930 B (without the TD fix) |
| `docs/session_state.md` | this file | older 5,881 B version |

Why `old historic project state.md` is not in the sandbox: the sandbox never
contained `PROJECT_STATE.md` (only a rebuilt backend slice), and the only way
to create it there is retyping 131 KB by hand, which cannot be byte-identical
(and the real file already holds a corrupted character in sections 13/16 that
would have to be reproduced). The faithful copy is a rename of the real file.

## Sync plan for the END of Phase 6 (do not do now)
1. Rename real `PROJECT_STATE.md` -> `old historic project state.md`
   (byte-exact move, no retyping).
2. Place the sandbox `PROJECT_STATE.md`, `docs/implementation_progress.md`,
   `docs/session_state.md`, and all verified Phase 6 code/tests in the repo;
   byte-check each against the sandbox.
3. Run the real `pytest tests/ -q`, `ruff check .`, `ruff format --check .`,
   `mypy app` where execution exists; then commit.

## Pending
- Phase 6 M2: **NOT started, NOT defined.** Wait for the owner's explicit
  instruction. Phase 7 not started.
- Open owner decisions: Phase 6 M2 scope; Phase 3 Step 5 scope.
- No real-repo test run exists for Phase 6 M1.
- Open debt: see `PROJECT_STATE.md` section 8 (TD #22-24 are new in Phase 6).

## Observations carried forward (not fixed)
- `.kilo/worktrees/brassy-plane/` is a second repo copy inside the project
  directory; not authoritative.
- `backend/.venv` appears to be CPython 3.13 vs documented 3.12 (compatible).
- `PROJECT_STATE.md` section 16 in the OLD file has a corrupted character
  ("Milestone 5 ?? RAG wiring"); irrelevant once the old file is archived.
