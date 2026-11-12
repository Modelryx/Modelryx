"""Small dependency-free read-path load smoke test."""

import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.request


def request_once(url: str, token: str, index: int) -> float:
    path = "/api/v1/operations/status" if index % 2 == 0 else "/api/v1/parts/search"
    body = None if index % 2 == 0 else json.dumps({"limit": 10}).encode()
    request = urllib.request.Request(
        url.rstrip("/") + path,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        response.read()
    return (time.perf_counter() - started) * 1000


def percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[min(len(values) - 1, round((len(values) - 1) * fraction))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(request_once, args.url, args.token, index)
            for index in range(args.requests)
        ]
        latencies = [future.result() for future in futures]
    print(
        f"requests={len(latencies)} mean_ms={statistics.mean(latencies):.1f} "
        f"p50_ms={percentile(latencies, 0.50):.1f} "
        f"p95_ms={percentile(latencies, 0.95):.1f} "
        f"p99_ms={percentile(latencies, 0.99):.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
