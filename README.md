# Turnstile Solver API

![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Framework](https://img.shields.io/badge/Framework-Quart-black)
![Browser](https://img.shields.io/badge/Browser-Patchright%20%2B%20Camoufox-success)
![Captcha](https://img.shields.io/badge/Captcha-Cloudflare%20Turnstile-orange)
![License](https://img.shields.io/badge/License-Unspecified-lightgrey.svg)
Production-oriented Cloudflare Turnstile solver API built with Quart, Patchright, and Camoufox.

This repository is a cleaned, public-safe showcase of a real deployment. It preserves the core implementation and practical improvements while excluding runtime state, private infrastructure details, and local machine-specific configuration.

## Features

- Async HTTP API powered by Quart
- Concurrent browser worker pool
- Support for `chromium`, `chrome`, `msedge`, and `camoufox`
- Optional browser fingerprint rotation from a curated config pool
- Optional proxy support via a local `proxies.txt`
- Per-task proxy override with safe redacted logging
- SQLite result storage with WAL mode enabled
- Automatic cleanup for older task results
- Optional helper flow for pages that gate Turnstile behind an address/email step
- Optional helper flow for pages that require clicking a verification trigger before the widget appears

## Public-safe scope

This showcase intentionally excludes:

- live VPS deployment details
- tunnel or reverse-proxy configuration
- runtime databases and logs
- local secrets, private addresses, and machine-specific state

## Project layout

- `api_solver.py` - main API server and solving workflow
- `browser_configs.py` - browser fingerprint configuration pool
- `db_results.py` - SQLite persistence helpers
- `requirements.txt` - Python dependencies
- `proxies.example.txt` - example proxy list format
- `.env.example` - optional environment configuration example

## Requirements

- Python 3.10+
- Linux, macOS, or Windows
- One supported browser backend:
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

Install the browser runtime for the backend you plan to use.

### Chromium

```bash
python -m patchright install chromium
```

### Google Chrome

Install Chrome on your system, then run the API with `--browser_type chrome`.

### Microsoft Edge

```bash
python -m patchright install msedge
```

### Camoufox

```bash
python -m camoufox fetch
```

## Configuration

If you need the optional address or email pre-submit helper flow:

```bash
cp .env.example .env
```

Environment variables:

- `TURNSTILE_LOGIN_ADDRESS` - optional value submitted to pages that gate verification behind an input named `address`

If you want proxy support, create a local `proxies.txt` file using one entry per line.

Supported formats:

```text
ip:port
ip:port:username:password
scheme://ip:port
scheme://username:password@ip:port
```

A sample format file is included as `proxies.example.txt`.

## Running

Basic local run:

```bash
python api_solver.py --browser_type chromium --host 127.0.0.1 --port 5000
```

Example with Chrome and debug logging:

```bash
python api_solver.py --browser_type chrome --host 127.0.0.1 --port 5000 --debug
```

## CLI arguments

| Argument | Description |
| --- | --- |
| `--no-headless` | Run the browser with a visible UI |
| `--useragent` | Provide a custom user-agent string |
| `--debug` | Enable verbose logging |
| `--browser_type` | Choose `chromium`, `chrome`, `msedge`, or `camoufox` |
| `--thread` | Number of browser workers |
| `--host` | Bind address |
| `--port` | Listening port |
| `--proxy` | Enable proxy usage from `proxies.txt` |
| `--random` | Randomize browser config from the bundled pool |
| `--browser` | Select an explicit browser name from the config pool |
| `--version` | Select an explicit browser version from the config pool |

## API

### 1. Create a solve task

```http
GET /turnstile?url=https://example.com&sitekey=0x4AAAAAAA
```

Optional query parameters:

- `action`
- `cdata`
- `proxy` - per-task proxy override. This works even when the server was not started with `--proxy`; `--proxy` is only needed for random selection from local `proxies.txt`.

Example:

```bash
curl "http://127.0.0.1:5000/turnstile?url=https://example.com&sitekey=0x4AAAAAAA"
```

Example with a per-task proxy:

```bash
curl "http://127.0.0.1:5000/turnstile?url=https://example.com&sitekey=0x4AAAAAAA&proxy=http://user:pass@127.0.0.1:8080"
```

Example response:

```json
{
  "errorId": 0,
  "taskId": "d2cbb257-9c37-4f9c-9bc7-1eaee72d96a8"
}
```

### 2. Poll the result

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

## Notes

- The root route (`/`) serves a simple built-in usage page.
- SQLite results are stored locally and are intentionally excluded from version control.
- This repository is meant to present the source cleanly, not to mirror live runtime state.

## Responsible use

Use this project only where you are authorized to automate, test, or evaluate the target flow.
