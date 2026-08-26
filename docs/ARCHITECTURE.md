# Architecture

High-level implementation map for the Turnstile & Cloudflare Solver API.

## Goal

Expose a high-throughput HTTP API that accepts Turnstile and Cloudflare interstitial solve requests, processes them asynchronously with a persistent browser worker pool, and lets clients poll for results.

## Main components

### 1. Quart application layer

Implemented in `api_solver.py`.

Responsibilities:

- register HTTP routes
- initialize runtime resources during startup
- enqueue background solve tasks
- translate stored task state into API responses

Public routes:

- `GET /` and `GET /docs` for a built-in landing page and documentation
- `GET /turnstile` to create a Turnstile solve task
- `GET /cf_clearance` to create a Cloudflare interstitial clearance task
- `GET /result` to poll a task result (token or clearance session bundle)

## 2. Browser worker pool

`TurnstileAPIServer` creates an `asyncio.Queue` of browser instances during startup.

Design notes:

- pool size is controlled by `--thread`
- supported backends: Patchright Chromium, Chrome, Edge, or Camoufox
- each worker receives either a fixed or randomized fingerprint config
- browser instances are reused across requests, while contexts/pages are created per solve with zero cold-start latency

## 3. Solve pipelines

### A. Turnstile fast-path route interception & real-page fallback
The Turnstile solve pipeline (`_solve_turnstile`):
1. **Fast-Path Route-Interception:** Serves a lightweight synthetic HTML stub (`build_route_html`, `route_glob`) directly on the target URL domain. Captures the Turnstile token in 2-3s without loading external website bloat.
2. **Real-Page Fallback:** If fast-path is inconclusive or times out, seamlessly falls back to full real-page navigation with resource-blocking filters.
3. **Physical Mouse Click Strategy:** Employs precise bounding-box physical coordinates `(box.x + 30, box.y + box.h/2)` via `page.mouse.click` to penetrate cross-engine Shadow DOM boundaries.

### B. Cloudflare clearance solver (`/cf_clearance`)
The interstitial solve pipeline (`_solve_cf_clearance`):
1. Navigates to IUAM, JS Challenge, or Managed Challenge protected pages.
2. Automatically detects CF challenge stage markers (`#challenge-form`, `.cf-browser-verification`, `challenges.cloudflare.com`).
3. Interacts with verification checkboxes and physical bounding boxes.
4. Extracts the complete session bundle:
   - `cf_clearance` cookie value
   - Complete cookie jar
   - Matching `User-Agent`
   - Complete request headers (`User-Agent`, `Accept-Language`, `sec-ch-ua`)
   - Measured `elapsed_time`

## 4. Persistence layer

Implemented in `db_results.py` using `aiosqlite`.

Stored state:

- initial task record with `CAPTCHA_NOT_READY`
- final solved token or clearance bundle
- failure marker when workers cannot solve
- task creation timestamp for cleanup

Database characteristics:

- file-based SQLite database at `results.db`
- WAL mode enabled with optimized pragmas (`cache_size=10000`, `temp_store=MEMORY`, `busy_timeout=30000`)
- periodic cleanup removes older records
- persistence is local-only and excluded from git

## 5. Fingerprint configuration pool

Implemented in `browser_configs.py`.

Responsibilities:

- store curated user-agent and `sec-ch-ua` combinations
- provide random or pinned browser/version combinations
- support practical browser-family switching for Chromium-based runs
