# DarkWeb Search API - Docker Image (optimized for dev)
FROM python:3.11-alpine

# Set working directory
WORKDIR /app

# Install minimal system dependencies
RUN apk add --no-cache --virtual .build-deps \
    gcc musl-dev libffi-dev openssl-dev && \
    apk add --no-cache curl && \
    python -m pip install --upgrade pip && \
    apk del .build-deps

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies and clean cache
RUN python -m pip install --no-cache-dir -r requirements.txt && \
    find /usr/local/lib/python3.11/site-packages -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -type f -name "*.pyc" -delete && \
    rm -rf /tmp/* /var/tmp/*

# Copy application code
COPY . .

# Create non-root user for security
RUN adduser -D -u 1000 apiuser && \
    mkdir -p /app/data && \
    chown -R apiuser:apiuser /app

USER apiuser

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/api/v1/health || exit 1

# Run FastAPI application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
