# Distributed URL Shortener Service

A **production-grade URL shortener** built with FastAPI, PostgreSQL, and Redis — designed to demonstrate distributed systems understanding, clean architecture, caching strategies, and performance optimization.

## 📋 Table of Contents

- [Problem Statement](#problem-statement)
- [Architecture](#architecture)
- [Database Schema](#database-schema)
- [Caching Strategy](#caching-strategy)
- [Rate Limiting](#rate-limiting)
- [API Reference](#api-reference)
- [Scaling Strategy](#scaling-strategy)
- [Benchmark Results](#benchmark-results)
- [Getting Started](#getting-started)
- [Testing](#testing)
- [CI/CD](#cicd)
- [Tradeoffs](#tradeoffs)
- [Future Improvements](#future-improvements)

---

## Problem Statement

URL shorteners appear simple but present real distributed systems challenges at scale:
- **Read-heavy workloads**: Redirects outnumber creations ~100:1
- **Low latency requirements**: Every redirect adds latency to user navigation
- **High availability**: Downtime = broken links across the internet
- **Collision handling**: Short codes must be unique across the entire system
- **Data durability**: URLs must persist reliably (people depend on them)

This project tackles these challenges with production-level patterns used at companies like Bitly, TinyURL, and Google.

---

## Architecture

### System Architecture Diagram

```
                           ┌──────────────────────────────────────────────┐
                           │              Load Balancer                   │
                           │           (Nginx / AWS ALB)                  │
                           └───────────────┬──────────────────────────────┘
                                           │
                    ┌──────────────────────┼─────────────────────┐
                    │                      │                     │
             ┌──────▼──────┐       ┌───────▼──────┐      ┌───────▼──────┐
             │  API Node 1 │       │  API Node 2  │      │  API Node N  │
             │  (FastAPI)  │       │  (FastAPI)   │      │  (FastAPI)   │
             └──────┬──────┘       └───────┬──────┘      └───────┬──────┘
                    │                      │                     │
                    └──────────────────────┼─────────────────────┘
                                           │
                    ┌──────────────────────┼─────────────────────┐
                    │                      │                     │
             ┌──────▼──────┐       ┌───────▼──────┐              │
             │    Redis    │       │  PostgreSQL  │              │
             │   (Cache)   │       │   (Primary)  │              │
             │  Sub-ms I/O │       │   1-5ms I/O  │              │
             └─────────────┘       └──────┬───────┘              │
                                          │                      │
                                   ┌──────▼──────┐        ┌──────▼─────┐
                                   │   Read      │        │    Read    │
                                   │   Replica 1 │        │  Replica 2 │
                                   └─────────────┘        └────────────┘
```

### Layered Architecture

```
┌────────────────────────────────────────────────────────┐
│  API Layer (routes.py)              ← HTTP concerns    │
│  - Request parsing, response formatting                │
│  - Route matching, status codes                        │
├────────────────────────────────────────────────────────┤
│  Service Layer (url_service.py)     ← Business logic   │
│  - URL creation, resolution, analytics                 │
│  - Caching orchestration, expiry enforcement           │
├────────────────────────────────────────────────────────┤
│  Data Layer (models, cache_service) ← Data access      │
│  - SQLAlchemy ORM, Redis client                        │
│  - Connection pooling, transactions                    │
├────────────────────────────────────────────────────────┤
│  Infrastructure (config, logging)   ← Cross-cutting    │
│  - Environment config, structured JSON logging         │
│  - Rate limiting, exception handling                   │
└────────────────────────────────────────────────────────┘
```

---

## Database Schema

```sql
CREATE TABLE urls (
    id              SERIAL PRIMARY KEY,
    original_url    TEXT NOT NULL,
    short_code      VARCHAR(20) UNIQUE NOT NULL,
    click_count     INTEGER DEFAULT 0 NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    expires_at      TIMESTAMPTZ,            -- NULL = never expires
    last_accessed_at TIMESTAMPTZ,
    is_active       BOOLEAN DEFAULT TRUE NOT NULL
);

-- Indexes for performance
CREATE UNIQUE INDEX ix_urls_short_code ON urls (short_code);   -- O(log n) redirect lookups
CREATE INDEX ix_urls_expires_at ON urls (expires_at);          -- Efficient expiry cleanup
CREATE INDEX ix_urls_is_active ON urls (is_active);            -- Active URL filtering
```

### Indexing Strategy

| Index | Purpose | Query Pattern |
|-------|---------|---------------|
| `ix_urls_short_code` (B-tree, unique) | Redirect lookups | `SELECT * FROM urls WHERE short_code = ?` |
| `ix_urls_expires_at` (B-tree) | Background cleanup | `DELETE FROM urls WHERE expires_at < NOW()` |
| `ix_urls_is_active` (B-tree) | Admin dashboard | `SELECT * FROM urls WHERE is_active = true` |

Without the `short_code` index, every redirect would do a **full table scan** — O(n) instead of O(log n). At 10M rows, this is the difference between 1ms and 500ms.

---

## Caching Strategy

### Cache-Aside (Lazy Loading) Pattern

```
READ PATH (Redirect):
┌────────┐     ┌──────────┐     ┌────────────┐
│ Client │────►│  Redis   │────►│ Return URL │  ← Cache HIT (sub-ms)
│        │     │ (check)  │     │            │
└────────┘     └────┬─────┘     └────────────┘
                    │ MISS
                    ▼
               ┌──────────┐     ┌────────────┐
               │PostgreSQL│────►│ Cache SET  │────► Return URL
               │ (query)  │     │ (with TTL) │
               └──────────┘     └────────────┘

WRITE PATH (Create URL):
┌────────┐     ┌──────────┐
│ Client │────►│PostgreSQL│  ← Write to DB only (no cache)
│        │     │ (insert) │
└────────┘     └──────────┘
```

### Why Cache-Aside?

| Alternative | Why Not |
|-------------|---------|
| **Write-Through** | Caches every URL on create, even cold ones. Wastes memory. |
| **Write-Behind** | Risks data loss if cache crashes before DB write. |
| **Read-Through** | Requires cache-DB integration. Less flexible. |
| **Cache-Aside** ✅ | Only caches on read. Hot URLs stay cached. Cold URLs don't waste space. |

### Dynamic TTL

```python
ttl = min(default_ttl, time_until_expiry)
```

This prevents serving **expired URLs from cache**. If a URL expires in 5 minutes but the default TTL is 1 hour, a static TTL would serve the expired URL for 55 minutes after expiry.

### Redis Eviction Policy

```yaml
maxmemory: 256mb
maxmemory-policy: allkeys-lru
```

When Redis fills up, **Least Recently Used** keys are evicted automatically. Hot URLs stay; cold URLs are evicted. No manual cleanup needed.

---

## Rate Limiting

### Token Bucket via Redis

```
Request → Extract IP → INCR "rate_limit:{ip}" → Over limit? → 429 Too Many Requests
                                                → Under limit? → Pass through
```

| Feature | Implementation |
|---------|---------------|
| **Algorithm** | Token bucket (sliding window counter) |
| **Storage** | Redis (atomic INCR + TTL) |
| **Scope** | Per-IP address |
| **Limit** | 100 req/60s (configurable) |
| **Scalability** | Shared across all API instances |
| **Failure mode** | Fail open (Redis down → allow all) |

### Why Redis-Based (not in-memory)?

In-memory rate limiting is per-process. With horizontal scaling, a user can bypass limits by hitting different instances. Redis provides a **shared counter** across all instances.

---

## API Reference

### `POST /shorten`

Create a shortened URL.

```json
// Request
{
  "url": "https://www.example.com/very/long/path",
  "expires_in_minutes": 60  // optional, 1–525600
}

// Response (201 Created)
{
  "short_code": "aBcDeFg",
  "short_url": "http://localhost:8000/aBcDeFg",
  "original_url": "https://www.example.com/very/long/path",
  "created_at": "2024-01-15T12:00:00Z",
  "expires_at": "2024-01-15T13:00:00Z"
}
```

### `GET /{short_code}`

Redirect to the original URL (HTTP 302).

| Response | Meaning |
|----------|---------|
| **302** | Redirect to original URL |
| **404** | Short code not found |
| **410 Gone** | URL has expired |
| **429** | Rate limit exceeded |

### `GET /{short_code}/stats`

Get analytics for a short URL.

```json
// Response (200 OK)
{
  "short_code": "aBcDeFg",
  "original_url": "https://www.example.com/very/long/path",
  "click_count": 42,
  "created_at": "2024-01-15T12:00:00Z",
  "expires_at": null,
  "last_accessed_at": "2024-01-16T08:30:00Z"
}
```

### `GET /health`

Health check for load balancers.

---

## Scaling Strategy

### How This System Scales to 10M+ URLs

#### 1. Horizontal Scaling (API Layer)

```
Load Balancer
├── API Instance 1 ──┐
├── API Instance 2 ──┤──► Shared Redis ──► Shared PostgreSQL
├── API Instance 3 ──┤
└── API Instance N ──┘
```

API servers are **stateless** — no session data or local state. Add more instances behind the load balancer to handle more traffic. Redis and PostgreSQL are shared.

#### 2. Database Sharding Strategy

For 100M+ URLs, shard the database by `short_code` hash:

```
short_code = "aBcDeFg"
shard_id = hash("aBcDeFg") % num_shards

Shard 0: short_codes hashing to 0 (URLs A-M)
Shard 1: short_codes hashing to 1 (URLs N-Z)
Shard 2: ...
```

**Consistent hashing** minimizes data movement when adding/removing shards. Only ~1/n of keys need to move (vs. rehashing everything).

#### 3. Read Replicas

```
Write Path:  API → Primary DB
Read Path:   API → Read Replica 1 / Read Replica 2
```

Since URL shorteners are read-heavy (~100:1 read/write ratio), offloading reads to replicas dramatically reduces primary DB load.

#### 4. Redis Clustering

For cache sizes exceeding single-node memory:

```
Redis Cluster (3 masters + 3 replicas)
├── Slot 0-5460    → Master 1 (Replica 1)
├── Slot 5461-10922 → Master 2 (Replica 2)  
└── Slot 10923-16383 → Master 3 (Replica 3)
```

Redis Cluster automatically shards data across nodes using hash slots. Client libraries handle routing transparently.

#### 5. Database Replication

```
Primary (writes) ──► Replica 1 (reads)
                 ──► Replica 2 (reads)
                 ──► Replica 3 (reads, standby failover)
```

Async replication provides eventual consistency (typically <100ms lag). For this use case, eventual consistency is acceptable — a URL created 100ms ago being temporarily unavailable on replicas is a fine tradeoff.

---

## Benchmark Results

### Test Configuration

- **Requests**: 1000 concurrent redirect requests
- **Concurrency**: 50 simultaneous connections
- **Test URLs**: 10 unique short codes

### Expected Results (representative values)

| Metric | With Redis Cache | Without Cache | Improvement |
|--------|:----------------:|:-------------:|:-----------:|
| **Avg Latency** | ~2ms | ~8ms | ~75% faster |
| **P95 Latency** | ~5ms | ~15ms | ~67% faster |
| **P99 Latency** | ~10ms | ~25ms | ~60% faster |
| **Throughput** | ~2000 req/s | ~500 req/s | ~4x higher |

> **Note**: Run `python benchmarks/load_test.py` against the Docker stack to generate actual numbers for your environment.

### Why the Improvement?

- **Redis**: Sub-millisecond reads from memory (~0.1ms)
- **PostgreSQL**: Disk-based reads with index lookup (~1-5ms)
- **Ratio**: ~95% of redirects hit the cache after warmup

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local development)

### Quick Start (Docker)

```bash
# Clone the repository
git clone https://github.com/your-username/distributed-url-shortener.git
cd distributed-url-shortener

# Start all services
docker compose up --build

# API is now available at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or .\venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment config
cp .env.example .env

# Start PostgreSQL and Redis (you need these running)
docker compose up postgres redis -d

# Run the API
uvicorn app.main:app --reload

# Run tests
python -m pytest tests/ -v
```

### Running Benchmarks

```bash
# Start the full stack
docker compose up --build -d

# Run benchmark
python benchmarks/load_test.py --requests 1000 --concurrency 50
```

---

## Testing

```bash
# Run all tests (55 tests)
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=app --cov-report=term-missing

# Run specific test file
python -m pytest tests/test_slug.py -v
```

### Test Summary

| File | Tests | Coverage |
|------|:-----:|----------|
| `test_slug.py` | 19 | Base62 encoding, uniqueness, collision retry |
| `test_cache.py` | 15 | Hit/miss, TTL, graceful degradation |
| `test_rate_limit.py` | 10 | Token bucket, 429 responses, fail-open |
| `test_api.py` | 11 | All endpoints, error handling, validation |
| **Total** | **55** | |

---

## CI/CD

GitHub Actions pipeline (`.github/workflows/ci.yml`):

1. **Lint** — Ruff (Rust-based, 100x faster than flake8)
2. **Test** — Pytest with coverage
3. **Build** — Docker image validation

Pipeline fails on lint errors or test failures. Docker image is built on every push to validate the Dockerfile.

---

## Tradeoffs

| Decision | Chosen | Alternative | Why |
|----------|--------|-------------|-----|
| **Redirect code** | 302 Temporary | 301 Permanent | 302 allows click tracking; 301 is cached by browsers forever |
| **Slug generation** | Random | Sequential | Random prevents enumeration attacks and hides creation order |
| **Caching** | Cache-aside | Write-through | Only caches hot data; doesn't waste memory on cold URLs |
| **Rate limiting** | Redis-based | In-memory | Works across multiple API instances; in-memory is per-process |
| **Click counting** | Direct DB write | Redis buffer | Simpler, durable; Redis buffer risks data loss on crash |
| **Expiry** | Check on read + 410 | Background cleanup | Immediate enforcement; cleanup is a secondary optimization |
| **Deletion** | Soft delete | Hard delete | Preserves audit trail and analytics data |

---

## Future Improvements

- [ ] **Custom aliases** — Let users choose their own short codes
- [ ] **QR code generation** — Auto-generate QR codes for short URLs
- [ ] **URL preview** — Show destination before redirecting (security feature)
- [ ] **Analytics dashboard** — Click-over-time charts, geographic data, referrer tracking
- [ ] **Bulk creation API** — Shorten multiple URLs in a single request
- [ ] **Webhook notifications** — Notify on click milestones
- [ ] **Alembic migrations** — Production-grade schema versioning
- [ ] **Prometheus metrics** — `url_redirects_total`, `redirect_latency_seconds`, `cache_hit_ratio`
- [ ] **Distributed tracing** — OpenTelemetry for cross-service request tracing
- [ ] **Multi-region deployment** — GeoDNS + regional caches for global low latency
- [ ] **Pre-generated slug pool** — ZooKeeper-backed slug pool for zero-collision writes
- [ ] **Event-driven analytics** — Kafka → ClickHouse pipeline for high-throughput click tracking

---

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| **API Framework** | FastAPI | Async-native, auto-docs, dependency injection |
| **Database** | PostgreSQL 16 | ACID compliance, excellent indexing, battle-tested |
| **Cache** | Redis 7 | Sub-ms reads, atomic operations, TTL support |
| **ORM** | SQLAlchemy 2.0 | Async support, mature ecosystem, type-safe |
| **Validation** | Pydantic v2 | Fast, type-safe, auto-generates JSON Schema |
| **Testing** | Pytest | Fixtures, parametrize, async support |
| **Linting** | Ruff | 100x faster than flake8, written in Rust |
| **Containerization** | Docker | Reproducible builds, isolated environments |
| **CI/CD** | GitHub Actions | Free for public repos, first-class Docker support |

---

## License

MIT
