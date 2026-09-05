# DarkWeb Search API - Docker Image (optimized for dev)
FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser + OS deps.
# Fixed path so the browser is findable after we drop to a non-root user below
# (default install path is under the current user's home, i.e. /root/... here).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium --with-deps

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 apiuser && \
    mkdir -p /app/data && \
    chown -R apiuser:apiuser /app /ms-playwright

USER apiuser

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/api/v1/health || exit 1

# Run FastAPI application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
