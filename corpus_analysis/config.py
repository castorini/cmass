#!/usr/bin/env python3
"""Paths and defaults for the corpus analysis pipeline.

Defaults point at ClimbMix, but nothing here is ClimbMix-specific: any directory of
parquet shards with a ``text`` column works. Override per run with ``--corpus-dir`` /
``--out-dir`` on any step, or globally with the ``CORPUS_DIR`` / ``CORPUS_OUT_DIR``
environment variables.

The pipeline writes ~2.5 TB at peak and takes about a day, so the paths are arguments
rather than constants edited in place -- pointing a 24-hour job at the wrong corpus is an
expensive mistake to make silently.
"""

import argparse
import os

# --- corpus (input): a directory of shard_NNNNN.parquet files with a `text` column
CORPUS_DIR = os.environ.get(
    "CORPUS_DIR", "/store/collections/climbmix-400b-shuffle-official")

# --- artifacts (output): everything the pipeline produces
OUT_DIR = os.environ.get(
    "CORPUS_OUT_DIR",
    "/store/collections/climbmix-400b-shuffle-official-corpus_analysis")

# --- near-duplicate parameters -------------------------------------------------------
# 512 permutations banded 73x7 gives candidate recall 1-(1-J^7)^73: 99.81% at J=0.7 and
# effectively 1.0 above J=0.8. Longer bands stay selective; more of them restore recall.
# Re-banding a fixed permutation count instead trades recall for selectivity along a
# strictly worse curve (42x6 on 256 perms: 99.48% recall for ~2x the low-similarity junk).
N_PERM = 512
BANDS = 73
ROWS = 7
SHINGLE_K = 5                 # word n-gram size for the Jaccard shingle sets
THRESHOLDS = (0.7, 0.8, 0.9)

# Floor for reported near-duplicate similarity. Below 0.7 candidate recall degrades fast
# (43.6% at J=0.5), so a lower threshold would be substantially incomplete while looking
# complete. Raise it freely; lowering it needs a wider banding.
MIN_THRESHOLD = 0.7

# Components larger than this cannot be verified pairwise within a bounded budget (cost is
# quadratic) and are excluded rather than clustered on weaker evidence.
MAX_COMPONENT = 40_000
MAX_BUCKET = 50_000

# Estimated-Jaccard gate applied before two documents may merge a component. Set well below
# MIN_THRESHOLD: at 64 permutations a true-0.7 pair sits ~5 sigma above 0.4, so real pairs
# are never gated out, while most low-similarity junk is. 0 disables it.
PREFILTER_THRESHOLD = 0.4
PREFILTER_PERMS = 64

# --- tokenizers ----------------------------------------------------------------------
# Llama-2 is the default: it is the tokenizer the ClimbMix authors state was used in the
# paper (arXiv:2504.13161), and the one under which the corpus measures its nominal 400B
# tokens (410.6B exactly). Qwen3 gives 350.0B and GPT-2 356.9B on the same text -- the
# corpus is identical, only the measuring stick differs.
TOKENIZERS = {
    "llama2": ("meta-llama/Llama-2",
               "https://huggingface.co/NousResearch/Llama-2-7b-hf/resolve/main/tokenizer.json"),
    "qwen3": ("Qwen/Qwen3-Embedding-0.6B",
              "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/resolve/main/tokenizer.json"),
    "gpt2": ("openai-community/gpt2",
             "https://huggingface.co/openai-community/gpt2/resolve/main/tokenizer.json"),
}
DEFAULT_TOKENIZER = "llama2"
TOKENIZER_DIR = os.environ.get(
    "CORPUS_TOKENIZER_DIR", os.path.expanduser("~/.cache/corpus_analysis/tokenizers"))


def token_dir(out_dir, tokenizer=DEFAULT_TOKENIZER):
    """Per-document token counts live under a tokenizer-suffixed name, so counts from
    several tokenizers coexist instead of overwriting one another."""
    return os.path.join(out_dir, f"token_lengths_{tokenizer}")


def length_stats_path(out_dir, tokenizer=DEFAULT_TOKENIZER):
    return os.path.join(out_dir, f"corpus_length_stats_{tokenizer}.json")


def near_dir(out_dir):
    return os.path.join(out_dir, "near_dup")


def base_parser(description, workers=None):
    """Argument parser carrying the options every step accepts."""
    ap = argparse.ArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus-dir", default=CORPUS_DIR,
                    help="directory of shard_NNNNN.parquet files (default: %(default)s)")
    ap.add_argument("--out-dir", default=OUT_DIR,
                    help="where artifacts are written (default: %(default)s)")
    if workers is not None:
        ap.add_argument("--workers", type=int, default=workers,
                        help="parallel worker processes (default: %(default)s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would run, with size estimates, and exit")
    return ap
