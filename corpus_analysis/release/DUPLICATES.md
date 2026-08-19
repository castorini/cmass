# corpus_duplicates — reference

Detail behind the `corpus_duplicates` config of
[castorini/cmass](https://huggingface.co/datasets/castorini/cmass). The dataset card there covers
what a row is and what each field holds; this covers how to read the values, how they were computed,
what is deliberately absent, and how the release was verified.

The corpus is ClimbMix as released at
[karpathy/climbmix-400b-shuffle](https://huggingface.co/datasets/karpathy/climbmix-400b-shuffle) —
553,240,576 documents. A doc id encodes a position in that release (`shard_{shard:05d}_{row:05d}`),
so ids resolve only against that copy.

## Reading the values

**Relationships are symmetric and both directions are stored.** If A lists B, B lists A. Filtering on
`doc_id` alone therefore gives a document's complete neighbourhood; no reverse lookup is needed.

**`jaccard` is exact, not estimated.** MinHash was used only to *generate candidate pairs*. Every
reported pair was then scored by computing the true Jaccard of the two documents' 5-gram word-shingle
sets. Filtering to a stricter threshold is a predicate, not a recomputation:

```python
strong = [n for n in row["near_duplicates"] if n["jaccard"] >= 0.9]
```

**`jaccard: 1.0` does not mean byte-identical.** It means identical after normalisation — lowercased,
non-alphanumeric characters collapsed — so two documents differing only in punctuation or whitespace
share a shingle set. Byte-identical documents appear in `exact_duplicates` instead. **The two lists
are always disjoint**: a document's own exact-duplicate group never appears among its near
duplicates.

**Near duplication is not transitive.** A ≈ B and B ≈ C does not imply A ≈ C. The data is stored as
neighbourhoods rather than clusters because any clustering loses information: a partition must break
some real pairs, and single-linkage merges documents that were never compared to each other. Derive
what you need — single-linkage is a BFS over these rows, complete-linkage a clique cover.

## How it was computed

**Exact duplicates**: groups sharing a SHA-256 over `text.strip()`, every group re-verified by direct
byte comparison. 16,845,444 documents in 5,910,028 groups.

**Near duplicates**: MinHash over 512 permutations, banded 73 × 7, giving candidate recall
`1-(1-J^7)^73` — 99.81% at J = 0.7 and effectively 1.0 above J = 0.8. Every candidate pair was then
scored by exact Jaccard over 5-gram word shingles, which both removes false positives and makes every
published score exact.

Full pipeline, tests and statistics: [`corpus_analysis/`](../).

## What is deliberately absent

**Nothing below Jaccard 0.7.** Candidate recall falls to 43.6% at J = 0.5, so a lower threshold would
be substantially incomplete while appearing complete.

**About 0.2% of true pairs at the threshold.** Recall *rises* as the threshold tightens, so a
filtered subset is more complete than the file as a whole.

**One component of 91,408 documents.** Verification cost is quadratic in component size; this one
exceeded the cap and was excluded rather than clustered on weaker evidence. Its sampled pairwise
Jaccard is 0.000 median — unrelated documents chained by shared boilerplate — so little real
duplication is lost.

**Documents shorter than 5 words.** With 5-gram shingles they produce an empty shingle set and cannot
be compared this way. They still appear via `exact_duplicates`, which does not depend on shingling.

## Corpus statistics

| | documents | share of corpus |
| --- | ---: | ---: |
| no duplicates (no row) | 334,174,396 | 60.40% |
| any duplicate | 219,066,180 | 39.60% |
| — with a near duplicate at J ≥ 0.7 | 215,379,056 | 38.93% |
| — exact duplicates only | 3,687,124 | 0.67% |
| in an exact-duplicate group | 16,845,444 | 3.04% |

436,322,424 undirected near-duplicate pairs at J ≥ 0.7. Duplication is overwhelmingly pairwise — 66%
of documents with a near duplicate have exactly one — though the largest exact group holds 6,377
byte-identical copies, and the heavily duplicated documents are boilerplate rather than content
(median length falls from about 460 tokens to 22 for documents with 1,000 or more copies).

## Verification

Group sizes were confirmed by a brute-force scan of the corpus sharing no code with the pipeline — no
SHA-256, no MinHash — which reproduced every sampled group exactly and counted 553,240,576 documents,
matching the corpus manifest. Sampled records were separately re-read from the corpus and re-scored
with a reference Jaccard implementation: no non-byte-identical exact pair, no Jaccard mismatch, no
entry below threshold, no list overlap.

The published files were then checked against the pipeline output: identical row count
(219,066,180) and an identical order-independent content checksum over every `doc_id`, count and
`(neighbour, jaccard)` pair, plus per-file sortedness and range coverage. Reproduce with
[`verify_release.py`](verify_release.py).

## Licence

Inherits the licence of the underlying ClimbMix corpus. These files contain only document identifiers
and similarity scores — no document text.
