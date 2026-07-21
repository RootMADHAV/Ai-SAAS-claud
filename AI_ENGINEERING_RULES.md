# AI Engineering Rules

> **Placeholder -- outline only.** The rule categories below are real and
> already in force (they have been followed since Milestone 1), but this
> file has not yet been written out in full. Until it is, `PROJECT_STATE.md`
> section 14 ("Rules that must never change during implementation") and
> section 10-12 (coding standards, testing requirements, development
> workflow) are the authoritative source.

## Categories to be expanded here

1. Code quality bar (production-quality only, full type hints,
   comprehensive docstrings)
2. Testing requirements (never skip tests, keep coverage/lint/type-check
   clean after every logical unit of work)
3. Architecture stability (do not redesign without a genuine
   implementation blocker; explain a blocker before adopting a fix)
4. Session scoping (never modify more than one milestone per session)
5. Technical debt policy (never introduce it without documenting it)
6. Work preservation (never recreate completed work; extend rather than
   rewrite unless rewriting is unavoidable)
7. Filesystem workflow rules (this project's filesystem is the source of
   truth, not the sandbox; see PROJECT_STATE.md section 13 for the
   Filesystem MCP's specific quirks and limitations)
