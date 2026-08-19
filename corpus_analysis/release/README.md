# Releasing the duplicate records

Turns the pipeline's per-document duplicate records into the `corpus_duplicates` config of
[castorini/cmass](https://huggingface.co/datasets/castorini/cmass) on HuggingFace.

## Why repartition at all

The pipeline writes 514 parquet parts keyed by internal component id. That is right for the
pipeline, which reads everything, and wrong for a published lookup table: a single part
holds documents from every corpus shard, unsorted, so no file or row group has a useful
`doc_id` range and a lookup must read all 4.7 GB.

Sorting by `doc_id` fixes that — one row group, ~4 MB — but costs storage, and the reason is
worth knowing before you change it:

| | component order | sorted by doc_id |
|---|---:|---:|
| size | 4.7 GB | 9.1 GB |
| read per `doc_id` lookup | 4,681 MB | 4 MB |

Component order places every member of a cluster on adjacent rows, so their neighbour lists
reference the same handful of ids: **15.9% of the neighbour ids in a row group are distinct,
each repeating ~6.3×**, which dictionary-encodes extremely well. Sorting scatters clusters,
so neighbour ids become effectively random across 553 M values — **97.5% distinct, ~1.0
repeats** — and the dictionary stops paying for itself. The `doc_id` column itself gets
*better* (5.9 → 3.6 B/row, sorted strings share prefixes), but that saving is swamped.

Compression settings do not rescue it: six configurations were measured, and the best
practical one recovers under 7%. There is one row ordering, and compression and lookup want
different ones.

The trade was taken in favour of lookup: 9 GB is unremarkable for HuggingFace, and engines
reading parquet over HTTP get the pruning without downloading anything.

## Steps

```bash
python release/repartition_for_hf.py --workers 24     # ~5 min, needs ~4.7 GB scratch
python release/verify_release.py    --workers 24      # ~15 min
```

`repartition_for_hf.py` runs three memory-bounded passes: count rows per corpus shard and
pick file boundaries with roughly equal *rows* (shards differ in how duplicated they are, so
equal shard spans would give lopsided files), scatter, then sort each bucket. It is
resumable and refuses to continue if the scatter loses a row.

`verify_release.py` is the gate before upload. Beyond row counts it computes an
order-independent content checksum — every row hashed over its `doc_id`, counts and each
`(neighbour, jaccard)` pair, summed mod 2^64 — which is blind to row order but not to a
dropped row, a duplicated row, a lost neighbour or a changed score. It also checks per-file
sortedness, range coverage, and compares sampled rows field for field against the source.

The run behind the current release:

```
rows     source 219,066,180   release 219,066,180        MATCH
checksum source 0x7e9559f702027737   release 0x7e9559f702027737   MATCH
32/32 files sorted, 0 overlaps, 200 sampled rows: 0 missing, 0 differing
RELEASE VERIFIED
```

## Upload

```bash
huggingface-cli upload castorini/cmass \
    /store/collections/climbmix-400b-shuffle-official-corpus_analysis/hf_release/corpus_duplicates \
    corpus_duplicates \
    --repo-type dataset
```

Then merge the YAML block from `DATASET_CARD.md` into the **repository root** `README.md`
frontmatter — HuggingFace reads configs from there, not from a subfolder — and append the
card body.

## Result

32 files, 9.1 GB, 219,066,180 rows, 6.82–6.87 M rows each (0.62% spread), sorted by
`doc_id`, 100,000-row groups.
