# Changelog

All notable changes to the Turnstile Solver API project.

## [2.0.0] - 2026-08-26

### Added
- **`/cf_clearance` Endpoint:** Full solver for Cloudflare Interstitials (IUAM, Managed Challenge, JS Challenge) extracting full session bundle (`cf_clearance`, `cookies`, `user_agent`, `headers`, `elapsed_time`).
- **Fast-Path Route Interception for `/turnstile`:** Synthetic route stub builder (`build_route_html`, `route_glob`) capturing Turnstile tokens in 2-3s without loading external website assets.
- **Physical Bounding-Box Mouse Clicks:** Sub-pixel coordinate clicking `(box.x + 30, box.y + box.h/2)` via `page.mouse.click` across Turnstile iframe boundaries.
- **Enhanced Documentation & UI:** Updated landing page at `/` and `/docs` with detailed endpoint usage and payload samples.
- **Comprehensive Test Suite:** Added 26 automated unit and endpoint integration tests in `tests/test_api_and_solver.py`.

### Changed
- Refactored `db_results.py` with `load_result_with_type` and enhanced SQLite WAL pragma tuning.
- Upgraded response payload schema on `/result` to return `elapsed_time` and complete clearance metadata.

## [1.0.0] - 2026-08-26

### Added
- Initial public release of Turnstile Solver API showcase.
- Async browser pool with Patchright and Camoufox support.
- SQLite WAL mode task storage.
- Per-task proxy overrides and dynamic browser fingerprinting.
