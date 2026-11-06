"""SSE benchmark. Only usage counts and timing are printed, never model text."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import statistics
import time
import urllib.request
import uuid


def request_once(base, token, prompt):
    prompt = uuid.uuid4().hex + '\n' + prompt
    started = time.perf_counter()
    first = None
    usage = None
    payload = json.dumps({'model': 'local-instruct', 'messages': [{'role': 'user', 'content': prompt}],
                          'max_tokens': 64, 'temperature': 0, 'stream': True,
                          'stream_options': {'include_usage': True}}).encode()
    req = urllib.request.Request(base + '/v1/chat/completions', data=payload,
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            for line in response:
                if not line.startswith(b'data: '):
                    continue
                raw = line[6:].strip()
                if raw == b'[DONE]':
                    break
                event = json.loads(raw)
                if event.get('error'):
                    return None
                for choice in event.get('choices', []):
                    if choice.get('delta', {}).get('content') and first is None:
                        first = time.perf_counter() - started
                if event.get('usage'):
                    usage = event['usage']
        if first is None or usage is None:
            return None
        return first, time.perf_counter() - started, usage['completion_tokens'], usage['prompt_tokens']
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--requests', type=int, default=8)
    parser.add_argument('--concurrency', type=int, default=2)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error('requests and concurrency must be positive')
    token = os.environ['API_TOKEN']
    print('| prompt | success | errors | input tokens mean | TTFT mean s | latency mean s | output tok/s | req/s |')
    print('|---|---:|---:|---:|---:|---:|---:|---:|')
    failed = False
    for label, prompt in [('short', 'Explain what a database index does.'),
                          ('long', 'Summarize this text: ' + 'A database stores records and uses indexes for searching. ' * 100)]:
        # Warm-up is excluded from the timed workload.
        if request_once(args.url, token, prompt) is None:
            raise SystemExit('Warm-up failed; check readiness and API_TOKEN')
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(lambda _: request_once(args.url, token, prompt), range(args.requests)))
        wall = time.perf_counter() - start
        good = [r for r in results if r is not None]
        errors = len(results) - len(good)
        failed |= errors > 0
        if good:
            ttft, latency, generated, inputs = zip(*good)
            print(f'| {label} | {len(good)} | {errors} | {statistics.mean(inputs):.1f} | '
                  f'{statistics.mean(ttft):.3f} | {statistics.mean(latency):.3f} | '
                  f'{sum(generated)/wall:.2f} | {len(good)/wall:.3f} |')
        else:
            print(f'| {label} | 0 | {errors} | n/a | n/a | n/a | 0 | 0 |')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
