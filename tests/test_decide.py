import json

import httpx
import pytest

from handdown import decide

Q = {"kind": {"type": "choice", "instructions": "Which?", "criteria": {"a": "A thing"}}}


def _cloudflare(answer, usage_today=0, refuse_after=None, seen=None):
    """A mocked Workers AI: one account, today's usage, and the run endpoint."""
    calls = {"run": 0}

    def handler(request):
        url = str(request.url)
        if url.endswith("/accounts"):
            return httpx.Response(200, json={"result": [{"id": "acc"}]})
        if url.endswith("/graphql"):
            groups = [{"sum": {"totalNeurons": usage_today}}]
            return httpx.Response(200, json={"data": {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": groups}]}}})
        calls["run"] += 1
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        if refuse_after is not None and calls["run"] > refuse_after:
            return httpx.Response(429, text="you have used up your daily free allocation of 10,000 neurons")
        return httpx.Response(200, json={"result": {"answers": answer(body), "usage": {"input_tokens": 1000}}, "success": True})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_every_choice_gets_a_way_out_unless_it_has_one():
    q = decide.with_other(
        {**Q, "yes": {"type": "noul", "instructions": "?"}, "has": {"type": "choice", "instructions": "?", "criteria": {"x": "X", "none": "None of these"}}}
    )
    assert q["kind"]["criteria"] == {"a": "A thing", **decide.OTHER}
    assert "criteria" not in q["yes"] and "other" not in q["has"]["criteria"]


def test_hosted_runs_need_a_token(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="CLOUDFLARE_API_TOKEN"):
        decide.Decider("clef")


def test_answers_count_tokens_cost_and_neurons(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    seen = []
    client, _ = _cloudflare(lambda body: {"kind": {"choice": "a", "probabilities": {"a": 0.9, "other": 0.1}}}, seen=seen)
    d = decide.Decider("clef-flash", client=client)
    rows = list(decide.decide(d, [{"id": 1, "state": "s"}, {"id": 2, "state": "t", "images": ["data:image/png;base64,AA=="]}], Q, parallel=2))
    assert [r["answers"]["kind"]["choice"] for r in rows] == ["a", "a"]
    assert seen[0]["questions"]["kind"]["criteria"]["other"] and seen[1]["images"] == ["data:image/png;base64,AA=="]
    assert d.input_tokens == 2000 and d.cost == pytest.approx(2000 * 0.038 / 1e6)
    assert d.neurons == pytest.approx(2000 * decide.neurons_per_token("clef-flash"))


def test_a_used_up_allocation_refuses_to_start(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    client, calls = _cloudflare(lambda body: {}, usage_today=40_436)
    with pytest.raises(decide.AllocationUsedUp):
        decide.Decider("clef", client=client)
    assert decide.Decider("clef", client=client, over_limit=True).neurons == 0  # --over-limit skips the check
    assert calls["run"] == 0


def test_the_own_count_stops_at_100_percent_before_cloudflare_does(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    # 9,990 neurons already used; one clef answer of 1,000 tokens is ~22 neurons
    client, calls = _cloudflare(lambda body: {"kind": {"choice": "a", "probabilities": {"a": 1.0}}}, usage_today=9_990)
    d = decide.Decider("clef", client=client)
    with pytest.raises(decide.AllocationUsedUp):
        list(decide.decide(d, [{"id": i, "state": "s"} for i in range(5)], Q, parallel=1))
    assert calls["run"] == 1


def test_a_refusal_skips_the_rest(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    client, calls = _cloudflare(lambda body: {"kind": {"choice": "a", "probabilities": {"a": 1.0}}}, refuse_after=1)
    d = decide.Decider("clef-flash", client=client)
    rows = []
    with pytest.raises(decide.AllocationUsedUp):
        for r in decide.decide(d, [{"id": i, "state": "s"} for i in range(4)], Q, parallel=1):
            rows.append(r)
    assert "answers" in rows[0] and "daily free allocation" in rows[1]["error"] and rows[3]["error"].startswith("skipped")
    assert calls["run"] == 2


def test_a_self_hosted_server_needs_no_token_and_has_no_cap(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    client, _ = _cloudflare(lambda body: {"kind": {"choice": "a", "probabilities": {"a": 1.0}}})
    d = decide.Decider("clef-flash", endpoint="http://runner:8082/v1/systemone", client=client)
    assert d.ask("s", Q)["kind"]["choice"] == "a" and d.cost == 0 and d.neurons == 0


def test_the_tree_asks_a_group_then_its_members_and_keeps_the_beam(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    tree = {
        "label": "root",
        "children": {
            "tools": {"label": "tools", "children": {"hammer": {"label": "hammer"}}},
            "animals": {"label": "animals", "children": {"cat": {"label": "cat"}, "dog": {"label": "dog"}}},
        },
    }

    def answer(body):
        labels = list(body["questions"]["node"]["criteria"].values())
        if labels[:2] == ["tools", "animals"]:
            return {"node": {"choice": "o1", "probabilities": {"o0": 0.3, "o1": 0.6, "other": 0.1}}}
        assert labels[:3] == ["cat", "dog", "hammer"]  # the likeliest group's members first
        return {"node": {"choice": "o1", "probabilities": {"o0": 0.2, "o1": 0.7, "o2": 0.05, "other": 0.05}}}

    client, calls = _cloudflare(answer)
    r = decide.decide_tree(decide.Decider("clef", client=client), tree, {"id": 7, "state": "s"}, "Which?", beam=2)
    assert r["class"] == "dog" and r["p"] == round(0.6 * 0.7, 4) and calls["run"] == 2
    assert [p["choice"] for p in r["path"]] == ["animals", "dog"]
