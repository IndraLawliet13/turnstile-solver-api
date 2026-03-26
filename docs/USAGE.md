# Usage Guide

Practical request patterns for the Turnstile Solver API showcase.

## API flow at a glance

1. Start the server.
2. Create a solve task with `/turnstile`.
3. Poll `/result?id=<task-id>` until the task is ready or fails.
4. Read the solved token from `solution.token`.

## Start the API

Basic local run:

```bash
python api_solver.py --browser_type chromium --host 127.0.0.1 --port 5000
```

Useful variants:

```bash
python api_solver.py --browser_type chrome --thread 6 --host 127.0.0.1 --port 5000
python api_solver.py --browser_type camoufox --debug --host 127.0.0.1 --port 5000
python api_solver.py --browser_type chromium --proxy --random --host 127.0.0.1 --port 5000
```

## Create a solve task

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

Some Turnstile integrations also use extra widget context.

- `action`
- `cdata`

Example:

```bash
curl "http://127.0.0.1:5000/turnstile?url=https://example.com/login&sitekey=0x4AAAAAAA&action=login&cdata=session123"
```

## Poll the result

```bash
curl "http://127.0.0.1:5000/result?id=d2cbb257-9c37-4f9c-9bc7-1eaee72d96a8"
```

Possible responses:

### Still processing

```json
{
  "status": "processing"
}
```

### Ready

```json
{
  "errorId": 0,
  "status": "ready",
  "solution": {
    "token": "0.xxxxx"
  }
}
```

### Failed

```json
{
  "errorId": 1,
  "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
  "errorDescription": "Workers could not solve the Captcha"
}
```

## Bash polling example

```bash
BASE_URL="http://127.0.0.1:5000"
TASK_ID=$(curl -s "$BASE_URL/turnstile?url=https://example.com&sitekey=0x4AAAAAAA" | python -c 'import sys, json; print(json.load(sys.stdin)["taskId"])')

while true; do
  RESPONSE=$(curl -s "$BASE_URL/result?id=$TASK_ID")
  echo "$RESPONSE"

  STATUS=$(printf '%s' "$RESPONSE" | python - <<'PY'
import json, sys
payload = json.load(sys.stdin)
print(payload.get("status") or payload.get("errorCode") or "")
PY
)

  if [ "$STATUS" = "ready" ] || [ "$STATUS" = "ERROR_CAPTCHA_UNSOLVABLE" ]; then
    break
  fi

  sleep 2
done
```

## Configuration patterns

### Optional address or email pre-submit flow

If the target page shows an input named `address` before the Turnstile widget appears:

```bash
cp .env.example .env
```

Set:

```dotenv
TURNSTILE_LOGIN_ADDRESS=you@example.com
```

The solver will try to fill the `address` field and submit the form before looking for the widget.

### Optional verification trigger click

The implementation also includes a helper path for pages that require clicking a verification button before Turnstile appears. Supported trigger patterns are source-driven and intentionally limited to the selectors already implemented in `api_solver.py`.

### Proxy list

If you start the server with `--proxy`, create a local `proxies.txt` file first:

```text
ip:port
ip:port:username:password
scheme://ip:port
scheme://username:password@ip:port
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
| `--host` | Bind address |
| `--port` | Listening port |

## Operational notes

- The first requests can be slower while workers initialize.
- Solve tasks are stored in local SQLite state via `results.db` and excluded from git.
- The API is asynchronous, but the solve result is still a polling flow rather than a websocket or callback flow.
- Browser installation happens outside `pip install -r requirements.txt`.

## Scope note

This is a public-safe showcase repo. It documents the local API contract and code behavior, but it does not include the owner's live deployment, reverse proxy, or private target-specific runtime state.
