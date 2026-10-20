#!/usr/bin/env python3
"""A stand-in corpus CLI over demo/corpus.json (invented documents), with the same two-command contract as a
real corpus service, so every tool can be tried offline:

  demo_cm.py search "<query>" [hits] [preview_chars]   -> {"results": [{"rank", "docid", "score", "preview"}]}
  demo_cm.py doc <docid>                               -> {"docid", "doc"}

Scoring is plain BM25 over lower-cased word tokens.
"""
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

DOCS = json.loads((Path(__file__).resolve().parent / "corpus.json").read_text())
TOK = re.compile(r"[a-z0-9]+")


def tokens(s):
    return TOK.findall(s.lower())


def search(query, hits=10, chars=220):
    toks = {d: Counter(tokens(t)) for d, t in DOCS.items()}
    avg = sum(sum(c.values()) for c in toks.values()) / len(toks)
    df = Counter(w for c in toks.values() for w in c)
    n, k1, b = len(toks), 1.2, 0.75
    scores = {}
    for d, c in toks.items():
        dl = sum(c.values())
        s = 0.0
        for w in set(tokens(query)):
            if w in c:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * c[w] * (k1 + 1) / (c[w] + k1 * (1 - b + b * dl / avg))
        if s > 0:
            scores[d] = s
    ranked = sorted(scores, key=lambda d: -scores[d])[:hits]
    return {"results": [{"rank": i + 1, "docid": d, "score": round(scores[d], 4),
                         "preview": " ".join(DOCS[d].split())[:chars]} for i, d in enumerate(ranked)]}


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "search":
        hits = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        chars = int(sys.argv[4]) if len(sys.argv) > 4 else 220
        print(json.dumps(search(sys.argv[2], hits, chars)))
    elif len(sys.argv) == 3 and sys.argv[1] == "doc":
        print(json.dumps({"docid": sys.argv[2], "doc": DOCS.get(sys.argv[2])}))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
