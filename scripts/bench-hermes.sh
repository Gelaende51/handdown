#!/bin/sh
# Benchmark Nous Portal models through Hermes Agent's proxy (`hermes proxy
# start`) on the host, where the catalog and the proxy are. Reads only; the
# answers land in work/bench/results/hermes-<model>.jsonl (resumes where it
# stopped). Usage: scripts/bench-hermes.sh [model ...]  (default: the free vision models)
set -u
cd "$(dirname "$0")/.."
export HERMES_PROXY_URL="${HERMES_PROXY_URL:-http://127.0.0.1:8645/v1}"
curl -sf "$HERMES_PROXY_URL/models" >/dev/null || { echo "proxy not reachable at $HERMES_PROXY_URL: run hermes proxy start"; exit 1; }
models="${*:-stepfun/step-3.7-flash:free meituan/longcat-2.5-preview:free stealth/space-bunny-alpha}"
for m in $models; do
  out="work/bench/results/hermes-$(echo "$m" | tr '/:' '__').jsonl"
  echo "$m -> $out"
  uv run handdown bench-run work/bench/sample.jsonl --model "hermes:$m@answer=512" --out "$out" > "data/bench-$(basename "$out" .jsonl).log" 2>&1 &
done
wait
for f in data/bench-hermes-*.log; do echo "$f: $(tail -1 "$f")"; done
