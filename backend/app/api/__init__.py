"""The API layer: FastAPI routers and the per-request dependency wiring
that connects them to the application layer's use cases and repositories.

Milestone 5: ``dependencies.py`` (the composition root's per-request DI
providers), ``v1/`` (the public ``/api/v1`` Scanning API), and
``internal/`` (the ``/internal`` health surface). ``app/main.py`` (one
level up) is the actual composition root that builds the FastAPI
application and mounts both routers -- this package holds what gets
mounted, not the app object itself.
"""
