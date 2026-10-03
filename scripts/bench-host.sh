#!/bin/sh
# Benchmark hosted models from the host, where the catalog is and where the
# container cannot reach: Nous Portal through Hermes Agent's proxy (`hermes
# proxy start`, PROVIDER=hermes, the default) or OpenCode Zen (PROVIDER=zen;
# free models work without a login, OPENCODE_API_KEY uses yours). Reads only;
# answers land in work/bench/results/<provider>-<model>.jsonl (resumes where
# it stopped). Usage: [PROVIDER=zen] scripts/bench-host.sh [model[@options] ...]
# (default: the provider's free vision models; options default to answer=512,
# e.g. step-3.7-flash:free@answer=512,think=off)
set -u
cd "$(dirname "$0")/.."
trap 'kill 0' INT TERM  # Ctrl+C also stops the runs in the background
provider="${PROVIDER:-hermes}"
if [ "$provider" = hermes ]; then
  export HERMES_PROXY_URL="${HERMES_PROXY_URL:-http://127.0.0.1:8645/v1}"
  curl -sf "$HERMES_PROXY_URL/models" >/dev/null || { echo "proxy not reachable at $HERMES_PROXY_URL: run hermes proxy start"; exit 1; }
  default="stepfun/step-3.7-flash:free meituan/longcat-2.5-preview:free stealth/space-bunny-alpha"
else
  default="longcat-2.5-preview-free space-bunny-free mimo-v2.6-flash-free fledge-alpha-free big-pickle"
fi
models="${*:-$default}"
for m in $models; do
  case "$m" in *@*) spec=$m ;; *) spec="$m@answer=512" ;; esac
  # options are part of the file name: another setting is another run, not a resume
  out="work/bench/results/$provider-$(echo "$m" | tr '/:@=,' '_____').jsonl"
  echo "$spec -> $out"
  uv run handdown bench-run work/bench/sample.jsonl --model "$provider:$spec" --out "$out" > "data/bench-$(basename "$out" .jsonl).log" 2>&1 &
done
wait
for f in data/bench-$provider-*.log; do echo "$f: $(tail -1 "$f")"; done
