"""The public API, versioned at ``/api/v1`` (PROJECT_STATE.md section 3's
"Public/internal API split").

Milestone 5: ``scans.py`` (the Scanning bounded context's HTTP surface --
create, run, and read scans) and ``schemas.py`` (its request/response
DTOs). Other bounded contexts (Findings, Assets, Reporting) have no
routes here yet -- see PROJECT_STATE.md's Milestone 5 design-decision
note on why: none of them has an application-layer use case built yet
(``application/{findings,assets,reporting}/`` remain empty scaffolds),
and Scanning is the one bounded context with use cases *and* an adapter
fully wired end-to-end (Milestones 3-4) for this milestone's routes to
call.
"""
