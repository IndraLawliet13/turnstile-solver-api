# Turnstile Solver API

A production-oriented Turnstile solver API built with Quart, Patchright, and Camoufox.

This repository is a cleaned showcase version of a solver actively used in a VPS environment. It keeps the real implementation and local improvements while removing runtime artifacts, local state, and machine-specific deployment details.

## Highlights

- Async HTTP API with Quart
- Multi-browser pool for concurrent solving
- Support for Chromium, Chrome, Edge, and Camoufox
- Random browser fingerprint rotation support
- Optional proxy support via `proxies.txt`
- SQLite result storage with WAL mode
- Automatic cleanup of old task results
- Optional helper flow for pages that require a preliminary address/email submission before the Turnstile widget appears

## What was customized beyond upstream

Compared with the original upstream base, this version includes a real-world helper flow for targets that:

- present an `address` input before verification, and/or
- require clicking a verification trigger button before the Turnstile widget is rendered

For public safety, the original local hardcoded login address was replaced with an environment variable.

## Project structure

- `api_solver.py` - main API server and solving workflow
- `browser_configs.py` - browser fingerprint configuration pool
- `db_results.py` - SQLite persistence helpers
- `requirements.txt` - Python dependencies
- `proxies.txt` - optional proxy list, not committed with secrets
- `.env.example` - example environment configuration

## Requirements

- Python 3.10+
- Linux, macOS, or Windows
- One of the supported browser backends:
  - Patchright Chromium
  - Google Chrome
  - Microsoft Edge
  - Camoufox

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install browser dependencies according to your chosen backend.

### Chromium

```bash
python -m patchright install chromium
```

### Google Chrome

Install Chrome on your system, then run with `--browser_type chrome`.

### Microsoft Edge

```bash
python -m patchright install msedge
```

### Camoufox

```bash
python -m camoufox fetch
```

## Configuration

Copy the example environment file if you need the optional login helper:

```bash
cp .env.example .env
```

Environment variables:

- `TURNSTILE_LOGIN_ADDRESS` - optional address/email submitted to pages that gate verification behind an input named `address`

If you use proxies, create `proxies.txt` locally. Supported formats:

```text
ip:port
ip:port:username:password
scheme://ip:port
scheme://username:password@ip:port
```

## Running

Basic local run:

```bash
python api_solver.py --browser_type chromium --host 127.0.0.1 --port 5000
```

Example with Chrome and debug logs:

```bash
python api_solver.py --browser_type chrome --host 127.0.0.1 --port 5000 --debug
```

## CLI arguments

| Argument | Description |
| --- | --- |
| `--no-headless` | Run browser with GUI |
| `--useragent` | Custom user-agent string |
| `--debug` | Enable verbose logs |
| `--browser_type` | `chromium`, `chrome`, `msedge`, `camoufox` |
| `--thread` | Number of browser workers |
| `--host` | Bind address |
| `--port` | Bind port |
| `--proxy` | Enable proxy usage from `proxies.txt` |
| `--random` | Randomize browser config from pool |
| `--browser` | Explicit browser name from config pool |
| `--version` | Explicit browser version from config pool |

## API

### Create solve task

```http
GET /turnstile?url=https://example.com&sitekey=0x4AAAAAAA
```

Optional query parameters:

- `action`
- `cdata`

Example response:

```json
{
  "errorId": 0,
  "taskId": "d2cbb257-9c37-4f9c-9bc7-1eaee72d96a8"
}
```

### Poll result

```http
GET /result?id=d2cbb257-9c37-4f9c-9bc7-1eaee72d96a8
```

Processing response:

```json
{
  "status": "processing"
}
```

Ready response:

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "token": "0.xxxxx"
  }
}
```

Failure response:

```json
{
  "errorId": 1,
  "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
  "errorDescription": "Workers could not solve the Captcha"
}
```

## Notes for deployment

- The original live deployment may run behind a local tunnel or reverse proxy. Those machine-specific details are intentionally not included here.
- SQLite result files, logs, caches, and compiled Python artifacts are excluded from version control.
- This repository is intended as a clean source showcase, not a dump of live runtime state.

## Disclaimer

Use responsibly and only where you are authorized to automate or test.
