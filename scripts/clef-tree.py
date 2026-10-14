"""A two-stage decision tree over the catalog's objects for Clef (at most 255
options per choice): WordNet hypernym subtrees, split top-down until each group
holds at most MAX objects, with branches under 6 objects pooled into their
parent's group. Writes work/bench/clef-tree.json, read by the cleftree: backend.

    uv run python scripts/clef-tree.py [out] [max per group]
"""

import collections
import json
import sys

from handdown.cli import Config, _conn
from handdown.concepts import wordnet

OUT = sys.argv[1] if len(sys.argv) > 1 else "work/bench/clef-tree.json"
MAX = int(sys.argv[2]) if len(sys.argv) > 2 else 60
POOL = 6

wn = wordnet()
conn = _conn(Config())
objects = sorted({r[0] for r in conn.execute("SELECT DISTINCT object_id FROM depiction WHERE method='ai' AND object_id LIKE 'wn:%'")})
path = {}
for o in objects:
    s = wn.synset(o[3:])
    path[o] = max(s.hypernym_paths(), key=len) if s.hypernym_paths() else [s]


def split(members, depth):
    """Members share path[:depth]; returns (anchor synset, members) groups."""
    anchor = path[members[0]][depth - 1] if depth else None
    if len(members) <= MAX:
        return [(anchor, members)]
    kids, here = collections.defaultdict(list), []
    for o in members:
        p = path[o]
        (kids[p[depth]] if len(p) > depth + 1 or (len(p) > depth and p[depth].name() != o[3:]) else here).append(o)
    if len(kids) == 1 and not here:  # a single chain: go deeper without a new group
        return split(next(iter(kids.values())), depth + 1)
    out, pool = [], list(here)
    for m in kids.values():
        if len(m) < POOL:
            pool += m
        else:
            out += split(m, depth + 1)
    if pool:
        out.append((anchor, pool))
    return out


def lemma(o):
    return wn.synset(o[3:]).lemma_names()[0].replace("_", " ")


def gloss(o):
    return f"{lemma(o)}: {' '.join(wn.synset(o[3:]).definition().split()[:8])}"


groups = split(objects, 0)
tree = {"label": "root", "children": {}}
for i, (anchor, members) in enumerate(groups):
    head = anchor.lemma_names()[0].replace("_", " ") if anchor else "entity"
    examples = ", ".join(list(dict.fromkeys(lemma(o) for o in members))[:6])
    tree["children"][f"g{i}"] = {"label": f"{head}: e.g. {examples}", "children": {o: {"label": gloss(o)} for o in members}}
json.dump(tree, open(OUT, "w"))
sizes = sorted(len(m) for _, m in groups)
print(f"{len(objects)} objects in {len(groups)} groups (sizes {sizes[0]}-{sizes[-1]}, median {sizes[len(sizes) // 2]}) -> {OUT}")
