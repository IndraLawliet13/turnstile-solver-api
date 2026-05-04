# Change Log

## 2026-05-05 - Per-task proxy hardening

- Touched files:
  - `api_solver.py`
  - `tests/test_proxy_config.py`
  - `README.md`
  - `docs/USAGE.md`
- What changed:
  - Added a dedicated `parse_proxy_config()` helper for supported proxy formats.
  - Made request-level `proxy=` work as a task override without requiring server-level `--proxy`.
  - Kept `--proxy` behavior for random selection from local `proxies.txt`.
  - Redacted proxy credentials in debug logs and stored task metadata.
  - Added unit coverage for plain, authenticated, URL-style, and backward-compatible proxy formats.
  - Kept GitHub Actions workflow untouched to avoid requiring workflow-scope auth during push.
- Why this exists:
  - The previous proxy path was brittle and did not actually support every documented proxy format. It also tied per-task proxy use to server-level file-proxy mode.
- Evidence:
  - Local `unittest` proxy parser coverage passes.
  - Local syntax compile for `api_solver.py`, `browser_configs.py`, and `db_results.py` passes.
- Do not casually remove:
  - The proxy parser helper and tests. They prevent regression in documented proxy formats and keep request-level proxy overrides deterministic.
