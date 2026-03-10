# =============================================================================
# Multi-Stage Dockerfile
# =============================================================================
#
# WHY MULTI-STAGE BUILDS?
#   Stage 1 (builder): install all dependencies including build tools
#   Stage 2 (production): copy only what's needed to run
#
#   Result:
#   - Single-stage image: ~500MB (includes pip, gcc, headers, etc.)
#   - Multi-stage image: ~150MB (just Python + your code + deps)
#
#   Smaller images = faster deploys, lower storage costs, smaller attack surface.
#
# WHY python:3.11-slim (not alpine)?
#   Alpine uses musl libc instead of glibc. This breaks:
#   - asyncpg (needs glibc)
#   - many scientific Python packages
#   slim is Debian-based with glibc, just without extra packages.

# ─── Stage 1: Builder ────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system dependencies needed for building Python packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first (Docker layer caching)
# WHY COPY REQUIREMENTS SEPARATELY?
#   Docker caches each layer. If requirements.txt hasn't changed,
#   Docker reuses the cached pip install layer — saving 2-3 minutes
#   on every build. Only code changes trigger a rebuild.
COPY requirements.txt .

# Install Python dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ─── Stage 2: Production ─────────────────────────────────────────────
FROM python:3.11-slim AS production

WORKDIR /app

# Install only runtime dependencies (no gcc, no build tools)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Copy the virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create a non-root user for security
# WHY NON-ROOT?
#   Running as root inside a container means:
#   - Container escape vulnerability = root access to host
#   - Any file created has root ownership
#   Running as non-root follows the principle of least privilege.
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

# Copy application code
COPY ./app /app/app

# Change ownership and switch to non-root user
RUN chown -R appuser:appuser /app
USER appuser

# Expose the application port
EXPOSE 8000

# Health check — Kubernetes/Docker Compose use this to verify container health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
# WHY uvicorn (not gunicorn)?
#   uvicorn is an ASGI server for async Python.
#   gunicorn is WSGI (synchronous). For async FastAPI, uvicorn is correct.
#   In production, you'd use: gunicorn -k uvicorn.workers.UvicornWorker
#   for multi-process handling. Single uvicorn is fine for this demo.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
