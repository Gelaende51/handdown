import json

import httpx
import pytest
from PIL import Image

from handdown import bench


def _client(handler):
    return httpx.Client(base_url=bench.GATEWAY, transport=httpx.MockTransport(handler))


def test_gateway_sends_the_image_and_keeps_usage():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "A floppy disk."}}], "usage": {"prompt_tokens": 300, "completion_tokens": 4, "cost": 0.0001}}
        )

    ask = bench.Gateway("google/gemini-x", client=_client(handler))
    [(answer, extra)] = ask([Image.new("RGB", (8, 8), "white")])
    assert answer == "floppy disk"
    assert extra == {"prompt_tokens": 300, "answer_tokens": 4, "cost_usd": 0.0001}
    content = seen[0]["messages"][0]["content"]
    assert seen[0]["model"] == "google/gemini-x" and content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_gateway_waits_on_rate_limits_and_stops_without_credit():
    replies = iter(
        [
            httpx.Response(429, headers={"retry-after": "3"}),
            httpx.Response(200, json={"choices": [{"message": {"content": "mug"}}]}),
            httpx.Response(402, text="no credit"),
        ]
    )
    waits = []
    ask = bench.Gateway("m", client=_client(lambda request: next(replies)), wait=waits.append)
    assert ask([Image.new("RGB", (8, 8))])[0][0] == "mug" and waits == [3.0]
    with pytest.raises(bench.CreditExhausted):
        ask([Image.new("RGB", (8, 8))])


def test_hosted_models_flags_free_and_vision_models():
    data = {
        "data": [
            {"id": "a/free-vision", "type": "language", "tags": ["vision"], "pricing": {"input": "0", "output": "0"}},
            {"id": "b/paid", "type": "language", "pricing": {"input": "0.0000001", "output": "0.0000004"}},
            {"id": "c/unpriced", "type": "embedding"},
        ]
    }
    rows = bench.hosted_models(_client(lambda request: httpx.Response(200, json=data)))
    assert [(r["id"], r["free"], r["tags"]) for r in rows] == [("a/free-vision", True, ["vision"]), ("b/paid", False, []), ("c/unpriced", False, [])]


def test_backend_spec_for_the_gateway(monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    ask, batch, size = bench.backend("gateway:google/gemini-x@size=128,answer=16")
    assert isinstance(ask, bench.Gateway) and ask.model == "google/gemini-x" and ask.answer_tokens == 16 and (batch, size) == (1, 128)


def test_backend_spec_for_nous(monkeypatch):
    monkeypatch.setenv("NOUS_API_KEY", "k")
    ask, _, _ = bench.backend("nous:Hermes-4-70B")
    assert ask.model == "Hermes-4-70B" and str(ask.client.base_url).startswith("https://inference-api.nousresearch.com/v1")


def test_nous_portal_listing_marks_free_tags_and_image_input():
    data = {
        "data": [
            {"id": "nvidia/nemotron:free", "architecture": {"input_modalities": ["text", "image"]}, "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
        ]
    }
    [row] = bench.hosted_models(_client(lambda request: httpx.Response(200, json=data)))
    assert row["free"] and row["tags"] == ["vision"] and row["input"] == "0.000001"


def test_hermes_proxy_needs_no_key_and_bypasses_the_http_proxy(monkeypatch):
    monkeypatch.setenv("HERMES_PROXY_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    ask, _, _ = bench.backend("hermes:Hermes-4-70B")
    assert str(ask.client.base_url) == "http://127.0.0.1:9999/v1/" and "authorization" not in ask.client.headers
    with pytest.raises(KeyError):
        bench.backend("gateway:x/y")


def test_media_models_without_token_prices_are_not_free():
    data = {
        "data": [
            {"id": "x/video", "type": "video", "pricing": {"video_duration_seconds": "0.1"}},
            {"id": "y/laya-free", "type": "language", "tags": ["free"], "pricing": {}},
        ]
    }
    assert [r["free"] for r in bench.hosted_models(_client(lambda request: httpx.Response(200, json=data)))] == [False, True]
