# Architecture

High-level implementation map for the Turnstile Solver API showcase.

## Goal

Expose a small HTTP API that accepts Turnstile solve requests, processes them asynchronously with a browser worker pool, and lets clients poll for results.

## Main components

### 1. Quart application layer

Implemented in `api_solver.py`.

Responsibilities:

- register HTTP routes
- initialize runtime resources during startup
- enqueue background solve tasks
- translate stored task state into API responses

Public routes:

- `GET /` for a built-in landing page
- `GET /turnstile` to create a solve task
- `GET /result` to poll a task result

## 2. Browser worker pool

`TurnstileAPIServer` creates an `asyncio.Queue` of browser instances during startup.

Design notes:

- pool size is controlled by `--thread`
- supported backends: Patchright Chromium, Chrome, Edge, or Camoufox
- each worker receives either a fixed or randomized fingerprint config
- browser instances are reused across requests, while contexts/pages are created per solve

This keeps task startup cheaper than launching a brand-new browser process for every request.

## 3. Solve pipeline

The core solve flow lives in `_solve_turnstile(...)`.

High-level sequence:

1. take one browser from the pool
2. create an isolated context and page
3. optionally configure proxy and browser fingerprint headers
4. navigate to the target URL
5. optionally submit a configured `address` field if the page gates the widget behind an input step
6. optionally click a verification trigger if the page hides Turnstile behind a button
7. watch for `input[name="cf-turnstile-response"]`
8. retry with helper click strategies and fallback overlay injection when needed
9. save success or failure into SQLite
10. close the context and return the browser to the pool

## 4. Persistence layer

Implemented in `db_results.py` using `aiosqlite`.

Stored state:

- initial task record with `CAPTCHA_NOT_READY`
- final solved token
- failure marker when workers cannot solve
- task creation timestamp for cleanup

Database characteristics:

- file-based SQLite database at `results.db`
- WAL mode enabled
- periodic cleanup removes older records
- persistence is local-only and intentionally excluded from git

## 5. Fingerprint configuration pool

Implemented in `browser_configs.py`.

Responsibilities:

- store curated user-agent and `sec-ch-ua` combinations
- provide random or pinned browser/version combinations
- support practical browser-family switching for Chromium-based runs

This is not a full anti-detection framework by itself, but it provides a cleaner input layer for browser profile selection.

## Request lifecycle

```text
Client
  |
  | GET /turnstile?url=...&sitekey=...
  v
Quart route creates task id
  |
  | save status=CAPTCHA_NOT_READY
  | spawn asyncio background task
  v
Browser worker solves target page
  |
  | save token or failure to SQLite
  v
Client polls GET /result?id=...
  |
  +--> processing
  +--> ready + solution.token
  +--> unsolvable error
```

## Concurrency model

- HTTP handling is asynchronous through Quart.
- Solve work is scheduled with `asyncio.create_task(...)`.
- Effective parallelism is bounded by the browser pool size.
- If all browsers are busy, new solve tasks wait for an available worker from the queue.

## Helper behaviors worth noting

### Address pre-submit helper

If `TURNSTILE_LOGIN_ADDRESS` is set and the page contains `input[name="address"]`, the solver tries to fill and submit that field before the main solve loop.

### Verification trigger helper

The solver also checks for a small set of known trigger selectors such as `Load Security Verification` before polling for the response token.

### Overlay fallback

If the expected token field still does not appear, the solver can inject a helper overlay path as a fallback strategy.

## Limitations

- Result delivery is polling-based, not push-based.
- The public repo does not ship the owner's live deployment topology.
- Some target pages may require extra custom steps not represented by the generic helpers in this codebase.
- Browser automation success still depends on the target site's behavior and environment.

## Files to read first

- `api_solver.py` for server and solve workflow
- `db_results.py` for persistence and cleanup behavior
- `browser_configs.py` for browser/fingerprint configuration data
- `docs/QUICKSTART.md` for local bring-up
- `docs/USAGE.md` for request examples and runtime options
