#!/usr/bin/env python3
"""Step 0 -- per-document token counts.

Stores raw per-document counts (uint32, one .npy per shard) rather than a histogram, so any
bucketisation is recomputed instantly afterwards and counts can be cross-tabulated against
duplicate status without re-tokenising the corpus. Output goes to a tokenizer-suffixed
directory, so counts from several tokenizers coexist rather than overwriting each other.

Counts exclude special tokens: this measures content length, not what a particular pooling
setup would feed a model. Including them adds exactly one token per document.

The tokenizer matters more than it looks. On ClimbMix the same 553,240,576 documents
measure 410.6B tokens under Llama-2 (32k vocab), 356.9B under GPT-2 (50k), and 350.0B
under Qwen3-Embedding (151k) -- a 17% spread on identical text. Llama-2 is the default
because it is what the ClimbMix authors state was used in their paper and the only one
that reproduces the corpus's nominal 400B.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# One rayon thread per worker. The tokenizers library spawns a full thread pool per
# process, so N workers x 96 threads thrashes the machine -- measured load average 1400+
# on 96 cores, with throughput far below the single-threaded-per-worker arrangement.
os.environ.setdefault("RAYON_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import json
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pyarrow.parquet as pq

import config
from common import load_manifest, log

_TOK = None


def tokenizer_json(name, explicit=None):
    """Path to a tokenizer.json, fetched from HuggingFace on first use and cached."""
    if explicit:
        return explicit
    if name not in config.TOKENIZERS:
        raise SystemExit(f"unknown tokenizer {name!r}; known: {', '.join(config.TOKENIZERS)}")
    _, url = config.TOKENIZERS[name]
    os.makedirs(config.TOKENIZER_DIR, exist_ok=True)
    path = os.path.join(config.TOKENIZER_DIR, f"{name}.tokenizer.json")
    if not os.path.exists(path):
        log(f"fetching {name} tokenizer from {url}")
        tmp = path + ".tmp"
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, path)
    return path


def _tok(path):
    global _TOK
    if _TOK is None:
        from tokenizers import Tokenizer
        _TOK = Tokenizer.from_file(path)
        _TOK.no_truncation()
        _TOK.no_padding()
    return _TOK


def do_shard(task):
    num, path, out_dir, tok_path, chunk = task
    dst = os.path.join(out_dir, f"shard_{num:05d}.npy")
    if os.path.exists(dst):
        return num, -1, 0.0
    t0 = time.time()
    tk = _tok(tok_path)
    texts = pq.read_table(path, columns=["text"]).column("text").to_pylist()
    out = np.empty(len(texts), dtype=np.uint32)
    for i in range(0, len(texts), chunk):
        sub = [x or "" for x in texts[i:i + chunk]]
        for j, e in enumerate(tk.encode_batch(sub, add_special_tokens=False)):
            out[i + j] = len(e.ids)
    # the temp name must end in .npy: np.save appends the extension otherwise, and the
    # rename then looks for a file that was never written
    tmp = dst + ".tmp.npy"
    np.save(tmp, out)
    os.replace(tmp, dst)
    return num, int(out.sum()), time.time() - t0


def main():
    ap = config.base_parser(__doc__, workers=48)
    ap.add_argument("--tokenizer", default=config.DEFAULT_TOKENIZER,
                    choices=sorted(config.TOKENIZERS),
                    help="which tokenizer to count with (default: %(default)s)")
    ap.add_argument("--tokenizer-json",
                    help="path to a tokenizer.json, overriding --tokenizer's download")
    ap.add_argument("--chunk", type=int, default=2000,
                    help="documents per encode_batch call (default: %(default)s)")
    a = ap.parse_args()

    man = load_manifest(a.out_dir)
    dst = config.token_dir(a.out_dir, a.tokenizer)
    tok_path = tokenizer_json(a.tokenizer, a.tokenizer_json)

    if a.dry_run:
        n = man["offsets"][-1]
        print(f"tokenizer      : {a.tokenizer}  ({config.TOKENIZERS[a.tokenizer][0]})")
        print(f"tokenizer.json : {tok_path}")
        print(f"shards         : {len(man['shards']):,}")
        print(f"documents      : {n:,}")
        print(f"output         : {dst}")
        print(f"disk           : ~{n * 4 / 1e9:.1f} GB of uint32 counts")
        print(f"workers        : {a.workers}")
        return

    os.makedirs(dst, exist_ok=True)
    tasks = [(s["num"], s["path"], dst, tok_path, a.chunk) for s in man["shards"]]
    t0 = time.time()
    done = cached = 0
    total = 0
    with ProcessPoolExecutor(a.workers) as ex:
        futs = [ex.submit(do_shard, t) for t in tasks]
        for f in as_completed(futs):
            num, n, _ = f.result()
            done += 1
            if n < 0:
                cached += 1
            else:
                total += n
            if done % 200 == 0 or done == len(tasks):
                el = time.time() - t0
                r = done / el if el else 0
                log(f"  {done}/{len(tasks)} ({cached} cached) {r:.2f} shard/s "
                    f"eta {(len(tasks)-done)/r/60 if r else 0:.0f}m "
                    f"tokens so far {total/1e9:.1f}B")

    stats = {"tokenizer": config.TOKENIZERS[a.tokenizer][0], "key": a.tokenizer,
             "add_special_tokens": False, "shards": len(tasks), "tokens_counted": total}
    with open(os.path.join(a.out_dir,
                           f"token_lengths_{a.tokenizer}_stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    log(f"done in {(time.time()-t0)/60:.1f}m, {total/1e9:.1f}B tokens")


if __name__ == "__main__":
    main()
