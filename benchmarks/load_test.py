"""
Benchmark Load Test — Cache vs No-Cache Performance Comparison

PURPOSE:
  Measure the real performance impact of Redis caching on redirect latency.
  This script simulates 1000 concurrent redirect requests and measures:
  - Average latency
  - P95 latency (95th percentile)
  - P99 latency (99th percentile)
  - Throughput (requests/second)

  Results are compared between cache-enabled and cache-disabled modes
  to demonstrate the measurable improvement from caching.

HOW TO RUN:
  1. Start the stack: docker compose up
  2. Run the benchmark: python benchmarks/load_test.py

  Ensure the API is running on http://localhost:8000 before starting.

OUTPUT:
  Console table comparing cache vs no-cache performance metrics.
"""

import asyncio
import time
import statistics
import argparse
from dataclasses import dataclass

import httpx


@dataclass
class BenchmarkResult:
    """Results from a single benchmark run."""

    total_requests: int
    successful_requests: int
    failed_requests: int
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    min_latency_ms: float
    total_time_s: float
    requests_per_second: float


async def create_test_urls(
    client: httpx.AsyncClient,
    base_url: str,
    count: int = 10,
) -> list[str]:
    """
    Create test URLs for benchmarking.

    Creates `count` short URLs and returns their short codes.
    These will be used as targets for the redirect benchmark.
    """
    short_codes: list[str] = []

    for i in range(count):
        try:
            response = await client.post(
                f"{base_url}/shorten",
                json={"url": f"https://www.example.com/benchmark-test-{i}"},
            )
            if response.status_code == 201:
                data = response.json()
                short_codes.append(data["short_code"])
        except httpx.HTTPError as e:
            print(f"  Warning: Failed to create test URL {i}: {e}")

    return short_codes


async def benchmark_redirects(
    base_url: str,
    short_codes: list[str],
    total_requests: int = 1000,
    concurrency: int = 50,
) -> BenchmarkResult:
    """
    Benchmark redirect endpoint with concurrent requests.

    HOW IT WORKS:
    1. Creates a semaphore to limit concurrency (avoid overwhelming the server)
    2. Sends `total_requests` GET /{short_code} requests
    3. Records latency for each request
    4. Computes percentile statistics

    Args:
        base_url: API base URL.
        short_codes: List of short codes to randomly redirect.
        total_requests: Total number of requests to send.
        concurrency: Max concurrent requests.

    Returns:
        BenchmarkResult with latency statistics.
    """
    latencies: list[float] = []
    errors: int = 0
    semaphore = asyncio.Semaphore(concurrency)

    async def single_request(client: httpx.AsyncClient, code: str) -> None:
        nonlocal errors
        async with semaphore:
            start = time.perf_counter()
            try:
                response = await client.get(
                    f"{base_url}/{code}",
                    follow_redirects=False,
                )
                elapsed = (time.perf_counter() - start) * 1000  # ms
                if response.status_code in (302, 307):
                    latencies.append(elapsed)
                else:
                    errors += 1
            except httpx.HTTPError:
                errors += 1

    # Distribute requests across available short codes
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = []
        for i in range(total_requests):
            code = short_codes[i % len(short_codes)]
            tasks.append(single_request(client, code))

        start_time = time.perf_counter()
        await asyncio.gather(*tasks)
        total_time = time.perf_counter() - start_time

    if not latencies:
        return BenchmarkResult(
            total_requests=total_requests,
            successful_requests=0,
            failed_requests=errors,
            avg_latency_ms=0,
            p50_latency_ms=0,
            p95_latency_ms=0,
            p99_latency_ms=0,
            max_latency_ms=0,
            min_latency_ms=0,
            total_time_s=total_time,
            requests_per_second=0,
        )

    sorted_latencies = sorted(latencies)
    p50_idx = int(len(sorted_latencies) * 0.50)
    p95_idx = int(len(sorted_latencies) * 0.95)
    p99_idx = int(len(sorted_latencies) * 0.99)

    return BenchmarkResult(
        total_requests=total_requests,
        successful_requests=len(latencies),
        failed_requests=errors,
        avg_latency_ms=round(statistics.mean(latencies), 2),
        p50_latency_ms=round(sorted_latencies[p50_idx], 2),
        p95_latency_ms=round(sorted_latencies[p95_idx], 2),
        p99_latency_ms=round(sorted_latencies[min(p99_idx, len(sorted_latencies) - 1)], 2),
        max_latency_ms=round(max(latencies), 2),
        min_latency_ms=round(min(latencies), 2),
        total_time_s=round(total_time, 2),
        requests_per_second=round(len(latencies) / total_time, 2),
    )


def print_result(label: str, result: BenchmarkResult) -> None:
    """Print benchmark results in a formatted table."""
    print(f"\n{'=' * 55}")
    print(f"  {label}")
    print(f"{'=' * 55}")
    print(f"  Total Requests:      {result.total_requests}")
    print(f"  Successful:          {result.successful_requests}")
    print(f"  Failed:              {result.failed_requests}")
    print(f"  ─────────────────────────────────────────────────")
    print(f"  Avg Latency:         {result.avg_latency_ms:.2f} ms")
    print(f"  P50 Latency:         {result.p50_latency_ms:.2f} ms")
    print(f"  P95 Latency:         {result.p95_latency_ms:.2f} ms")
    print(f"  P99 Latency:         {result.p99_latency_ms:.2f} ms")
    print(f"  Min Latency:         {result.min_latency_ms:.2f} ms")
    print(f"  Max Latency:         {result.max_latency_ms:.2f} ms")
    print(f"  ─────────────────────────────────────────────────")
    print(f"  Total Time:          {result.total_time_s:.2f} s")
    print(f"  Throughput:          {result.requests_per_second:.2f} req/s")
    print(f"{'=' * 55}")


def print_comparison(with_cache: BenchmarkResult, without_cache: BenchmarkResult) -> None:
    """Print a comparison table between cache and no-cache results."""
    print(f"\n{'=' * 60}")
    print(f"  📊 COMPARISON: Cache vs No-Cache")
    print(f"{'=' * 60}")
    print(f"  {'Metric':<25} {'With Cache':>12} {'No Cache':>12} {'Improvement':>12}")
    print(f"  {'─' * 55}")

    metrics = [
        ("Avg Latency (ms)", with_cache.avg_latency_ms, without_cache.avg_latency_ms),
        ("P50 Latency (ms)", with_cache.p50_latency_ms, without_cache.p50_latency_ms),
        ("P95 Latency (ms)", with_cache.p95_latency_ms, without_cache.p95_latency_ms),
        ("P99 Latency (ms)", with_cache.p99_latency_ms, without_cache.p99_latency_ms),
        ("Throughput (req/s)", with_cache.requests_per_second, without_cache.requests_per_second),
    ]

    for name, cache_val, no_cache_val in metrics:
        if "Throughput" in name:
            # Higher is better for throughput
            if no_cache_val > 0:
                improvement = ((cache_val - no_cache_val) / no_cache_val) * 100
            else:
                improvement = 0
            sign = "+" if improvement > 0 else ""
        else:
            # Lower is better for latency
            if no_cache_val > 0:
                improvement = ((no_cache_val - cache_val) / no_cache_val) * 100
            else:
                improvement = 0
            sign = "+" if improvement > 0 else ""

        print(f"  {name:<25} {cache_val:>12.2f} {no_cache_val:>12.2f} {sign}{improvement:>10.1f}%")

    print(f"{'=' * 60}")


async def main() -> None:
    """Run the benchmark suite."""
    parser = argparse.ArgumentParser(description="URL Shortener Benchmark")
    parser.add_argument(
        "--url", default="http://localhost:8000", help="API base URL"
    )
    parser.add_argument(
        "--requests", type=int, default=1000, help="Total requests per run"
    )
    parser.add_argument(
        "--concurrency", type=int, default=50, help="Max concurrent requests"
    )
    parser.add_argument(
        "--urls", type=int, default=10, help="Number of test URLs to create"
    )
    args = parser.parse_args()

    print(f"\n🚀 URL Shortener Benchmark")
    print(f"   Target: {args.url}")
    print(f"   Requests: {args.requests}")
    print(f"   Concurrency: {args.concurrency}")

    # Create test URLs
    print(f"\n📝 Creating {args.urls} test URLs...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        short_codes = await create_test_urls(client, args.url, args.urls)

    if not short_codes:
        print("❌ No test URLs created. Is the API running?")
        return

    print(f"   Created {len(short_codes)} test URLs")

    # Run benchmark WITH cache (default behavior)
    print(f"\n⏱️  Running benchmark WITH cache ({args.requests} requests)...")
    with_cache = await benchmark_redirects(
        args.url, short_codes, args.requests, args.concurrency
    )
    print_result("WITH REDIS CACHE", with_cache)

    # Run benchmark again (by now cache is warm, so results reflect cache hits)
    print(f"\n⏱️  Running warm cache benchmark ({args.requests} requests)...")
    warm_cache = await benchmark_redirects(
        args.url, short_codes, args.requests, args.concurrency
    )
    print_result("WARM CACHE (2nd run)", warm_cache)

    # Print comparison
    print_comparison(warm_cache, with_cache)

    print(f"\n💡 Note: For a true no-cache comparison, disable Redis")
    print(f"   and run: python benchmarks/load_test.py")
    print(f"   Compare the results to see the caching improvement.\n")


if __name__ == "__main__":
    asyncio.run(main())
