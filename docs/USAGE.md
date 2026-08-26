# Usage Guide

Practical request patterns for the Turnstile & Cloudflare Solver API.

## API flow at a glance

1. Start the server.
2. Create a solve task with `/turnstile` or `/cf_clearance`.
3. Poll `/result?id=<task-id>` until the task is ready or fails.
4. Read the solved token or session clearance bundle from `solution`.

## Start the API

Basic local run:

```bash
python api_solver.py --browser_type chromium --host 127.0.0.1 --port 5000
```

Useful variants:

```bash
python api_solver.py --browser_type chrome --thread 4 --host 127.0.0.1 --port 5000
python api_solver.py --browser_type camoufox --debug --host 127.0.0.1 --port 5000
python api_solver.py --browser_type chromium --proxy --random --host 127.0.0.1 --port 5000
```

## 1. Create a Turnstile solve task

Minimum required parameters:

- `url`
- `sitekey`

Example:

```bash
curl "http://127.0.0.1:5000/turnstile?url=https://example.com&sitekey=0x4AAAAAAA"
```

Typical response:

```json
{
  "errorId": 0,
  "taskId": "d2cbb257-9c37-4f9c-9bc7-1eaee72d96a8"
}
```

### Optional parameters

- `action`: Turnstile action parameter
- `cdata`: Turnstile cdata payload
- `proxy`: Per-task proxy override (`http://user:pass@ip:port` or `socks5://ip:port`)

Example with action and proxy:

```bash
curl "http://127.0.0.1:5000/turnstile?url=https://example.com/login&sitekey=0x4AAAAAAA&action=login&proxy=socks5://127.0.0.1:9050"
```

## 2. Create a Cloudflare Clearance solve task

Parameters:

- `url` (Required): Target URL protected by Cloudflare Interstitials / IUAM.
- `proxy` (Optional): Per-task proxy override.

Example:

```bash
curl "http://127.0.0.1:5000/cf_clearance?url=https://protected-site.com"
```

Typical response:

```json
{
  "errorId": 0,
  "taskId": "9a38f712-4123-4f81-a901-7cba12398451"
}
```

## 3. Poll the result

```bash
curl "http://127.0.0.1:5000/result?id=<taskId>"
```

### Possible responses:

#### A. Still processing
```json
{
  "status": "processing"
}
```

#### B. Turnstile Ready
```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "token": "0.xxxxx",
    "elapsed_time": 2.14
  }
}
```

#### C. CF Clearance Ready
```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "cf_clearance": "07vN210...",
    "cookies": [
      {
        "name": "cf_clearance",
        "value": "07vN210...",
        "domain": ".protected-site.com",
        "path": "/"
      }
    ],
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
    "headers": {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
      "Accept-Language": "en-US,en;q=0.9",
      "sec-ch-ua": "\"Google Chrome\";v=\"139\"..."
    },
    "elapsed_time": 4.12
  }
}
```

#### D. Failed
```json
{
  "errorId": 1,
  "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
  "errorDescription": "Workers could not solve the Captcha"
}
```

## CLI reference

| Argument | Purpose |
| --- | --- |
| `--browser_type` | Choose `chromium`, `chrome`, `msedge`, or `camoufox` |
| `--thread` | Number of browser workers kept in the pool |
| `--no-headless` | Show the browser UI |
| `--debug` | Print verbose solve logs |
| `--proxy` | Enable proxy usage from `proxies.txt` |
| `--random` | Randomize browser config from the bundled pool |
| `--browser` | Pin a specific browser profile family from `browser_configs.py` |
| `--version` | Pin a specific browser version from `browser_configs.py` |
| `--useragent` | Override the user-agent string manually |
| `--host` | Bind address (Default: `0.0.0.0`) |
| `--port` | Listening port (Default: `5072`) |
