"""Scanner adapter framework: ActiveScanner/ImportScanner and the adapter
registry.

Milestone 3: ``base_scanner.py`` (``run_scanner_subprocess`` -- the
argument-list/timeout/non-root subprocess guarantee every
``ActiveScanner`` adapter must route through). The adapter registry
(code-level, per PROJECT_STATE.md section 3) is not yet built -- three
adapters exist so far (``adapters/nuclei/``, ``adapters/nmap/``,
``adapters/sqlmap/``), and a registry of three entries still has no
behavior worth writing yet."""
