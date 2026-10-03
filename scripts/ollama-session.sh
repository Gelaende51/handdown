#!/bin/sh
# Start an Ollama session on GitHub Actions and wait until the host can reach
# it over Tailscale. Needs gh and tailscale on the host (same tailnet as the
# TS_AUTHKEY secret). Usage: [CONTEXT=32768] scripts/ollama-session.sh [model ...]
set -eu
repo=Gelaende51/handdown
models="${*:-qwen3-vl:4b-instruct}"
url=http://handdown-ollama:11434
gh workflow run ollama-session.yml -R "$repo" -f model="$models" -f context="${CONTEXT:-16384}"
echo "started; waiting for $url (runner start, Ollama install and model download take a few minutes)"
i=0
until curl -sf "$url/api/version" >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 120 ] && { echo "not reachable after 20 minutes: see gh run list -R $repo -w ollama-session"; exit 1; }
  sleep 10
done
until curl -sf "$url/api/tags" | grep -q '"name"'; do sleep 10; done
echo "ready: $url"
curl -s "$url/api/tags" | python3 -c 'import json,sys; [print("  model:", m["name"]) for m in json.load(sys.stdin)["models"]]'
cat <<USAGE
  ollama:   OLLAMA_HOST=$url ollama run ${models%% *}
  OpenAI:   base URL $url/v1 (any API key)
  stop:     gh run cancel -R $repo \$(gh run list -R $repo -w ollama-session -s in_progress --json databaseId -q '.[0].databaseId')
USAGE
