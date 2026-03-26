# Quick Start

Fast path for running the showcase API locally.

## 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 2. Install a browser backend

Choose one backend.

### Chromium via Patchright

```bash
python -m patchright install chromium
```

### Microsoft Edge via Patchright

```bash
python -m patchright install msedge
```

### Camoufox

```bash
python -m camoufox fetch
```

Or install Google Chrome on the host and run with `--browser_type chrome`.

## 3. Optional configuration

If a target site asks for an address or email before the Turnstile widget appears:

```bash
cp .env.example .env
```

Set:

```dotenv
TURNSTILE_LOGIN_ADDRESS=you@example.com
```

If you want proxy rotation, create `proxies.txt` from the included example:

```bash
cp proxies.example.txt proxies.txt
```

## 4. Run the API

```bash
python api_solver.py --browser_type chromium --host 127.0.0.1 --port 5000
```

## 5. Verify the server

```bash
curl http://127.0.0.1:5000/
```

Create a solve task:

```bash
curl "http://127.0.0.1:5000/turnstile?url=https://example.com&sitekey=0x4AAAAAAA"
```

Poll the result:

```bash
curl "http://127.0.0.1:5000/result?id=<task-id>"
```

## Notes

- The first solve can be slower because browser workers need to initialize.
- This public repo intentionally omits live deployment state, runtime logs, and private infrastructure details.
- The repository currently has no open-source license grant. See the main README for the explicit status note.
