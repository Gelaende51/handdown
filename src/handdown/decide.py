"""Decision models: Cloudflare's Clef (Workers AI or a self-hosted llama.cpp
server) answering typed questions instead of generating text. A choice, a
yes/no (noul) or a score per item, returned as probabilities; built for the
same simple judgement over many items (docs/vision-benchmark.md, *Decision
models*).

- Every choice question gets a way out, "other", unless one of its options
  already is one. It also lets a single real candidate be asked (choice needs
  two). Clef takes at most 255 options per choice.
- More classes than that go through a tree (``decide_tree``): a group first,
  then the detail inside it, one call per stage, up to 254^stages classes.
- Workers AI's free plan allows 10,000 neurons a day (00:00 UTC). Cloudflare's
  own count lags ~20 minutes (it once let 404 % through), so a run keeps its
  own: today's reading at the start plus each answer's tokens. It refuses to
  start at 100 % and stops there, unless ``over_limit``.

Credentials come from the environment only: CLOUDFLARE_API_TOKEN (Workers AI
permission) and, optionally, CLOUDFLARE_ACCOUNT_ID (otherwise looked up with
the token). A self-hosted server (``endpoint``) needs neither."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API = "https://api.cloudflare.com/client/v4"
PRICE = {"clef": 0.24, "clef-flash": 0.038}  # $ per million input tokens; output is free
FREE_NEURONS = 10_000
OTHER = {"other": "None of these: something else, or it cannot be told"}
MAX_OPTIONS = 254  # 255 with "other"
EXHAUSTED = 75  # exit code once the daily allocation is used up


def neurons_per_token(model: str) -> float:
    """The token price over Workers AI's $0.011 per 1,000 neurons (clef 0.0218,
    clef-flash 0.00345, as the usage API reports)."""
    return PRICE.get(model, PRICE["clef"]) / 1e6 / (0.011 / 1000)


class AllocationUsedUp(RuntimeError):
    """Workers AI's daily free allocation is gone: every further call fails until 00:00 UTC."""


def _is_way_out(key: str, text: Any) -> bool:
    return bool(re.fullmatch(r"other|none|unknown|else", key, re.I) or re.match(r"(other|none of these|something else)\b", str(text), re.I))


def with_other(questions: dict[str, Any]) -> dict[str, Any]:
    """Each choice question with "other" added, unless it has a way out already."""
    out = {}
    for qid, q in questions.items():
        if q.get("type") == "choice" and not any(_is_way_out(k, v) for k, v in (q.get("criteria") or {}).items()):
            q = {**q, "criteria": {**q["criteria"], **OTHER}}
        out[qid] = q
    return out


def data_url(path: str | Path) -> str:
    """An image file as the data URL both Workers AI and llama.cpp require."""
    if str(path).startswith("data:"):
        return str(path)
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode()}"


def _client(timeout: float = 120) -> Any:
    import httpx

    return httpx.Client(timeout=timeout)


def account_id(token: str, client: Any = None) -> str:
    """CLOUDFLARE_ACCOUNT_ID, or the only account the token reaches."""
    if os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
        return os.environ["CLOUDFLARE_ACCOUNT_ID"]
    r = (client or _client()).get(f"{API}/accounts", headers={"Authorization": f"Bearer {token}"})
    accounts = r.json().get("result") or []
    if r.status_code != 200 or len(accounts) != 1:
        raise RuntimeError(f"set CLOUDFLARE_ACCOUNT_ID: the token reaches {len(accounts)} accounts (HTTP {r.status_code})")
    return accounts[0]["id"]


def usage_today(token: str, account: str, client: Any = None) -> int:
    """Today's Workers AI neurons on the account (Cloudflare's count, ~20 minutes behind)."""
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    query = (
        f'{{viewer{{accounts(filter:{{accountTag:"{account}"}}){{aiInferenceAdaptiveGroups(limit:100,filter:{{date_geq:"{day}"}}){{sum{{totalNeurons}}}}}}}}}}'
    )
    r = (client or _client()).post(f"{API}/graphql", headers={"Authorization": f"Bearer {token}"}, json={"query": query})
    groups = r.json()["data"]["viewer"]["accounts"][0]["aiInferenceAdaptiveGroups"]
    return round(sum(g["sum"]["totalNeurons"] for g in groups))


class Decider:
    """One model behind ``ask(state, questions, images)``: Workers AI by
    default, or a self-hosted llama.cpp ``/v1/systemone`` with ``endpoint``."""

    def __init__(self, model: str = "clef", endpoint: str | None = None, over_limit: bool = False, client: Any = None, log: Callable[[str], None] = print):
        self.model, self.client, self.log = model, client or _client(), log
        self.hosted = endpoint is None
        self.input_tokens = 0
        self.neurons = 0.0
        self.over_limit = over_limit or not self.hosted
        if self.hosted:
            token = os.environ.get("CLOUDFLARE_API_TOKEN")
            if not token:
                raise RuntimeError("CLOUDFLARE_API_TOKEN is not set (on the host: cradle secret set CLOUDFLARE_API_TOKEN)")
            account = account_id(token, self.client)
            self.url = f"{API}/accounts/{account}/ai/run/@cf/cloudflare/{model}"
            self.headers = {"Authorization": f"Bearer {token}"}
            if not over_limit:
                try:
                    self.neurons = float(usage_today(token, account, self.client))
                except Exception as e:  # a missing figure must not stop the work
                    log(f"decide: could not read today's Workers AI usage ({e}); going ahead without the check")
                if self.neurons >= FREE_NEURONS:
                    raise AllocationUsedUp(
                        f"today's free Workers AI allocation is used up ({self.neurons:.0f} of {FREE_NEURONS} neurons); it resets at 00:00 UTC"
                    )
        else:
            self.url, self.headers = endpoint, {}

    def estimate(self, items: Iterable[dict[str, Any]], questions: dict[str, Any] | None = None) -> float:
        """Rough neurons for a run: ~4 characters a token, ~300 tokens an image."""
        chars = sum(len(json.dumps(i.get("state"))) + len(json.dumps(i.get("questions") or questions or {})) + 1200 * len(i.get("images") or []) for i in items)
        return chars / 4 * neurons_per_token(self.model)

    def ask(self, state: Any, questions: dict[str, Any], images: list[str] | None = None) -> dict[str, Any]:
        if not self.over_limit and self.neurons >= FREE_NEURONS:
            raise AllocationUsedUp("today's free allocation reached 100 % by this run's own count")
        body: dict[str, Any] = {"model": self.model, "state": state, "questions": with_other(questions)}
        if images:
            body["images"] = [data_url(i) for i in images]
        for attempt in range(5):
            r = self.client.post(self.url, headers=self.headers, json=body)
            if r.status_code == 200:
                res = r.json()
                res = res.get("result", res)  # Workers AI wraps the answer in {result, success, errors}
                used = (res.get("usage") or {}).get("input_tokens") or 0
                self.input_tokens += used
                if self.hosted:
                    self.neurons += used * neurons_per_token(self.model)
                return res["answers"]
            if r.status_code == 429 and "daily free allocation" in r.text:
                raise AllocationUsedUp("Workers AI refused: daily free allocation used up (resets 00:00 UTC)")
            if (r.status_code == 429 or r.status_code >= 500) and attempt < 4:
                time.sleep(0.5 * 2**attempt)
                continue
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        raise RuntimeError("unreachable")

    @property
    def cost(self) -> float:
        return self.input_tokens * PRICE.get(self.model, PRICE["clef"]) / 1e6 if self.hosted else 0.0


def decide(decider: Decider, items: list[dict[str, Any]], questions: dict[str, Any] | None = None, parallel: int = 8) -> Iterable[dict[str, Any]]:
    """{id, answers} or {id, error} per item, in order. Items carry ``state``
    and optionally their own ``questions`` and ``images``. Once the allocation
    is used up, the remaining items come back as errors without a call."""
    stop = {"used_up": False}

    def one(item: dict[str, Any]) -> dict[str, Any]:
        head = {"id": item.get("id"), **({"meta": item["meta"]} if "meta" in item else {})}
        if stop["used_up"]:
            return {**head, "error": "skipped: daily free allocation used up"}
        try:
            return {**head, "answers": decider.ask(item.get("state"), item.get("questions") or questions or {}, item.get("images"))}
        except AllocationUsedUp as e:
            stop["used_up"] = True
            return {**head, "error": str(e)}
        except Exception as e:  # one failed item must not end the run
            return {**head, "error": str(e)}

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        yield from pool.map(one, items)
    if stop["used_up"]:
        raise AllocationUsedUp("daily free allocation used up; the rest was skipped")


def decide_tree(decider: Decider, tree: dict[str, Any], item: dict[str, Any], instructions: str, beam: int = 1) -> dict[str, Any]:
    """Walk ``tree`` ({"label", "children": {id: node}}; no children = a class)
    for one item: at each stage one choice among the children of the nodes in
    play; with ``beam`` > 1 the likeliest nodes stay in play and the next stage
    chooses among all their children. "other" stops where it is. An item's
    ``meta`` (e.g. the pictogram's source_id and original_id) is passed on."""
    head = {"id": item.get("id"), **({"meta": item["meta"]} if "meta" in item else {})}
    frontier: list[tuple[str, dict[str, Any], float]] = [("", tree, 1.0)]
    path: list[dict[str, Any]] = []
    while "children" in frontier[0][1]:
        options: list[tuple[str, dict[str, Any], float]] = []
        for _, node, p in frontier:
            kids = [(cid, child, p) for cid, child in (node.get("children") or {}).items()]
            if options and len(options) + len(kids) > MAX_OPTIONS:
                break
            options += kids
        if not options:
            break
        criteria = {f"o{i}": child["label"] for i, (_, child, _) in enumerate(options)}
        a = decider.ask(item.get("state"), {"node": {"type": "choice", "instructions": instructions, "criteria": criteria}}, item.get("images"))["node"]
        if a["choice"] == "other":
            path.append({"options": len(options), "choice": "other", "p": a["probabilities"]["other"]})
            return {**head, "class": None, "other": True, "reached": frontier[0][0], "path": path}
        ranked = sorted(((k, v) for k, v in a["probabilities"].items() if k != "other"), key=lambda kv: -kv[1])[:beam]
        frontier = [(options[int(k[1:])][0], options[int(k[1:])][1], v * options[int(k[1:])][2]) for k, v in ranked]
        path.append({"options": len(options), "choice": frontier[0][0], "p": a["probabilities"][a["choice"]]})
    key, node, p = frontier[0]
    return {**head, "class": key, "label": node.get("label"), "p": round(p, 4), "path": path}


def log_run(path: Path, **fields: Any) -> None:
    """One line per run in ``path`` (work/decide-usage.jsonl): what was judged,
    tokens, cost, so the takeaways can say where a decision model saved tokens."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"date": datetime.now(UTC).isoformat(timespec="seconds"), **fields}) + "\n")
