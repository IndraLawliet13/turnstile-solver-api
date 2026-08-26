# Turnstile & Cloudflare Solver API

![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Framework](https://img.shields.io/badge/Framework-Quart-black)
![Browser](https://img.shields.io/badge/Browser-Patchright%20%2B%20Camoufox-success)
![Captcha](https://img.shields.io/badge/Captcha-Cloudflare%20Turnstile%20%26%20IUAM-orange)
![Storage](https://img.shields.io/badge/Storage-SQLite%20(WAL)-blueviolet)

Production-grade asynchronous Cloudflare Solver API built with Quart, Patchright, and Camoufox. Engineered for ultra-high throughput, low latency solving of Cloudflare Turnstile widgets, IUAM (Under Attack Mode), Managed Challenges, and JS Challenges.

---

## 🚀 Key Features

- **⚡ Fast-Path Route Interception (`/turnstile`):** Synthetic HTML stub generation (`build_route_html`, `route_glob`) resolving challenges in ~2-3s without loading full bloated target pages, with seamless automatic fallback to full realpage navigation.
- **🛡️ Interstitial & IUAM Challenge Solver (`/cf_clearance`):** Automatically solves Cloudflare Interstitials (IUAM, Managed Challenge, JS Challenge) and extracts full session clearance bundles (`cf_clearance` cookie, complete cookies list, user_agent, and request headers).
- **🖱️ Bounding-Box Physical Mouse Click Strategy:** Bypasses Shadow DOM and cross-origin iframe security restrictions via viewport coordinate calculation `(box.x + 30, box.y + box.h/2)` and `page.mouse.click` emulation with human-like jitter.
- **🔄 Zero Cold-Start Browser Worker Queue Pool:** Persistent browser instances in an `asyncio.Queue` pool reusing browser processes and spinning up lightweight ephemeral contexts on demand.
- **💾 High-Performance SQLite WAL Persistence:** Async SQLite with write-ahead logging (WAL), optimized PRAGMA cache, sub-millisecond query execution, and automatic cleanup of stale tasks.
- **🎭 Multi-Browser & TLS Fingerprint Rotation:** Dynamic User-Agent and `Sec-CH-UA` client hint injection for Chromium, Chrome, Edge, and Camoufox.
- **🌐 Robust Proxy Handling:** Support for HTTP/HTTPS/SOCKS5 proxies with credentials, random pool rotation, per-request override, and safe secret masking in logs.

---

## 📂 Project Structure

- `api_solver.py` — Main Quart server, route endpoints, solver pipelines (Fast-Path Route Interception, Realpage Solver, CF Clearance Solver, Bounding-Box Mouse Clicker).
- `browser_configs.py` — Curated pool of browser configurations (`User-Agent`, `Sec-CH-UA`).
- `db_results.py` — Async SQLite database layer with WAL mode and lifecycle management.
- `tests/` — Test suite for unit tests, proxy config validation, database operations, and endpoint routing.
- `requirements.txt` — Python dependencies.
- `proxies.example.txt` — Example proxy list format.

---

## 🛠️ Installation & Setup

```bash
# 1. Clone repository and setup venv
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Install browser dependencies (Patchright Chromium / Camoufox)
python -m patchright install chromium
# Optional: python -m camoufox fetch
```

---

## 🖥️ Running the Solver API

```bash
# Basic run on 0.0.0.0:5072
python api_solver.py --browser_type chromium --thread 2 --port 5072

# Run with debug logging and randomized browser fingerprints
python api_solver.py --browser_type chromium --thread 4 --random --debug

# Run with proxy support from proxies.txt
python api_solver.py --browser_type chromium --proxy --thread 2
```

### CLI Arguments

| Argument | Description | Default |
| --- | --- | --- |
| `--host` | Bind IP address | `0.0.0.0` |
| `--port` | Listening port | `5072` |
| `--browser_type` | Browser engine (`chromium`, `chrome`, `msedge`, `camoufox`) | `chromium` |
| `--thread` | Number of concurrent persistent browser worker instances | `2` |
| `--no-headless` | Run browser with visible GUI | `False` |
| `--random` | Randomize User-Agent & `Sec-CH-UA` from curated pool | `False` |
| `--proxy` | Enable random proxy rotation from `proxies.txt` | `False` |
| `--debug` | Enable verbose debug logging | `False` |

---

## 📖 Interactive API Documentation

- **Swagger UI:** `http://localhost:5072/swagger` or `http://localhost:5072/docs`
- **OpenAPI 3.0.3 Specification:** `http://localhost:5072/openapi.json`

The server provides a built-in dark-themed Swagger UI with interactive parameter exploration and request execution.

---

## 📡 API Reference

### 1. Solve Turnstile Widget (`/turnstile`)

Queues a Turnstile solve task using the fast-path route interception pipeline with automatic realpage fallback.

**Request:**
```http
GET /turnstile?url=https://example.com/login&sitekey=0x4AAAAAAAJ5XXXXXXXXX&action=login&proxy=http://user:pass@host:port
```

**Parameters:**
- `url` *(required)*: The target page URL hosting Turnstile.
- `sitekey` *(required)*: Turnstile sitekey.
- `action` *(optional)*: Turnstile action parameter.
- `cdata` *(optional)*: Turnstile cdata parameter.
- `proxy` *(optional)*: Per-task proxy override.

**Response:**
```json
{
  "errorId": 0,
  "taskId": "7a35368a-6b45-4202-aef2-b5e09f5bc390"
}
```

---

### 2. Solve Cloudflare Interstitials & Extract Clearance (`/cf_clearance`)

Solves Cloudflare IUAM, Under Attack Mode, Managed Challenge, and JS Challenge interstitials.

**Request:**
```http
GET /cf_clearance?url=https://nowsecure.nl&proxy=http://user:pass@host:port
```

**Parameters:**
- `url` *(required)*: The Cloudflare protected URL.
- `proxy` *(optional)*: Per-task proxy override.

**Response:**
```json
{
  "errorId": 0,
  "taskId": "c1f7b889-1065-4f47-8a8b-3023e1e23351"
}
```

---

### 3. Poll Task Result (`/result`)

Retrieves the status and solved payload for a task ID.

**Request:**
```http
GET /result?id=7a35368a-6b45-4202-aef2-b5e09f5bc390
```

**Processing Response:**
```json
{
  "status": "processing"
}
```

**Ready Response (Turnstile):**
```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "token": "0.xxxxx.yyyyy.zzzzz"
  }
}
```

**Ready Response (CF Clearance):**
```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "cf_clearance": "v1.abc123xyz...",
    "cookies": [
      {
        "name": "cf_clearance",
        "value": "v1.abc123xyz...",
        "domain": ".example.com",
        "path": "/",
        "expires": 1787773892,
        "httpOnly": true,
        "secure": true
      }
    ],
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...",
    "headers": {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...",
      "Accept-Language": "en-US,en;q=0.9",
      "sec-ch-ua": "\"Not;A=Brand\";v=\"99\", \"Google Chrome\";v=\"139\", \"Chromium\";v=\"139\""
    },
    "elapsed_time": 4.12
  }
}
```

**Error Response:**
```json
{
  "errorId": 1,
  "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
  "errorDescription": "Workers could not solve Cloudflare challenge"
}
```
