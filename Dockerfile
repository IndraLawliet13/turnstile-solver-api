# Production Multi-stage / Lightweight Dockerfile for Turnstile Solver API
FROM python:3.11-slim-bookworm AS base

# Environment configurations
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PATCHRIGHT_BROWSERS_PATH=/ms-playwright \
    PORT=5072 \
    HOST=0.0.0.0

WORKDIR /app

# Install system dependencies required by Chromium and Patchright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    xdg-utils \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies specifications
COPY requirements.txt pyproject.toml /app/

# Install Python requirements and patchright browser
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt && \
    python -m patchright install --with-deps chromium

# Copy application source code
COPY api_solver.py browser_configs.py db_results.py /app/
COPY README.md LICENSE-STATUS.md /app/

# Expose API port
EXPOSE 5072

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5072/openapi.json || exit 1

# Start server
ENTRYPOINT ["python", "api_solver.py"]
CMD ["--host", "0.0.0.0", "--port", "5072", "--browser_type", "chromium", "--thread", "2"]
