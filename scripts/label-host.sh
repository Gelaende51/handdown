#!/bin/sh
# Fast first pass on the host: LongCat 2.5 Preview (free; 60 % agreement with
# Claude's labels against Qwen3-VL's 63 %, 3.4 s instead of 55-85 s per image)
# names the depictions nobody asked about yet, composite parts first. The
# providers share the list: Nous Portal through the Hermes proxy (`hermes
# proxy start`) and OpenCode Zen (free without a login). Resumes where it
# stopped; Ctrl+C stops everything.
#   uv run handdown fast-jobs                  # the list (data/fast/items.jsonl)
#   scripts/label-host.sh                      # or: PROVIDERS=zen scripts/label-host.sh
# The answers (data/fast/answers-*.jsonl) are applied as provisional labels:
#   uv run handdown vlm-apply --fast data/fast/answers-*.jsonl
set -u
cd "$(dirname "$0")/.."
trap 'kill 0' INT TERM
export HERMES_PROXY_URL="${HERMES_PROXY_URL:-http://127.0.0.1:8645/v1}"
providers="${PROVIDERS:-hermes zen}"
case " $providers " in *" hermes "*)
  curl -sf "$HERMES_PROXY_URL/models" >/dev/null || { echo "Hermes proxy not reachable at $HERMES_PROXY_URL: run hermes proxy start, or PROVIDERS=zen"; exit 1; } ;;
esac
[ -s data/fast/items.jsonl ] || { echo "no list: run uv run handdown fast-jobs"; exit 1; }
n=$(echo $providers | wc -w); i=0
for p in $providers; do
  case $p in
    hermes) model="meituan/longcat-2.5-preview:free" ;;
    zen) model="longcat-2.5-preview-free" ;;
    *) echo "unknown provider $p"; exit 1 ;;
  esac
  echo "$p ($model): every ${n}th item from $i -> data/fast/answers-$p.jsonl"
  uv run handdown bench-run data/fast/items.jsonl --model "$p:$model@answer=64" --variants norm,original \
    --shard "$i/$n" --out "data/fast/answers-$p.jsonl" > "data/fast/$p.log" 2>&1 &
  i=$((i + 1))
done
wait
for p in $providers; do echo "$p: $(tail -1 data/fast/$p.log)"; done
