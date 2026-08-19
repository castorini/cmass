# corpus_duplicates — dataset card

Text to publish with the `corpus_duplicates` config of
[castorini/cmass](https://huggingface.co/datasets/castorini/cmass).

HuggingFace reads configs from the **repository root** `README.md`, so the YAML below must
be merged into that file's existing frontmatter (alongside the `queries` and `qrels`
configs) rather than dropped in as a new file. The body can be appended to the root README,
or kept as `corpus_duplicates/README.md` for anyone browsing the folder.

---

## YAML to merge into the root README frontmatter

```yaml
configs:
  - config_name: corpus_duplicates
    data_files:
      - split: corpus
        path: corpus_duplicates/corpus-*.parquet
```

---

## Body

### corpus_duplicates

Exact and near-duplicate relationships between documents of the **ClimbMix** corpus
(`nvidia/ClimbMix`, 553,240,576 documents), as computed for CMASS.

One row per document that has at least one duplicate: **219,066,180 rows** in 32 parquet
files, 9.1 GB. A document with no duplicates has **no row** — absence is the answer, not an
omission. Emitting all 553 M documents with two empty arrays would roughly triple the size
to convey nothing.

```python
from datasets import load_dataset
ds = load_dataset("castorini/cmass", "corpus_duplicates")["corpus"]
```

#### Schema

| field | type | |
|---|---|---|
| `doc_id` | `string` | `shard_00188_76292` — fixed width, `shard_{shard:05d}_{row:05d}` |
| `exact_duplicates` | `list<string>` | byte-identical documents |
| `near_duplicates` | `list<struct<doc_id: string, jaccard: float32>>` | Jaccard ≥ 0.7, descending |
| `n_exact` | `int32` | `len(exact_duplicates)` |
| `n_near` | `int32` | `len(near_duplicates)` |

```json
{"doc_id": "shard_00877_14962",
 "exact_duplicates": ["shard_03268_70493", "shard_05228_44024"],
 "near_duplicates": [{"doc_id": "shard_01150_63127", "jaccard": 1.0},
                     {"doc_id": "shard_03774_17051", "jaccard": 0.9638}],
 "n_exact": 2, "n_near": 17}
```

#### File layout

Rows are **sorted by `doc_id`**, and each file covers a contiguous slice of the corpus:

| file | rows | range |
|---|---:|---|
| `corpus-00000-of-00032.parquet` | 6,866,370 | `shard_00000_00004` … `shard_00204_83964` |
| `corpus-00001-of-00032.parquet` | 6,831,016 | `shard_00205_00002` … `shard_00408_84991` |
| … | … | … |
| `corpus-00031-of-00032.parquet` | 6,830,626 | `shard_06339_00001` … `shard_06542_84989` |

Files hold 6.82–6.87 M rows each (0.62% spread) in 100,000-row groups. Because the data is
sorted, parquet row-group statistics let a reader skip everything irrelevant: looking up one
`doc_id` touches **one row group — about 4 MB of the 9.1 GB**. Engines that read parquet
over HTTP (DuckDB, Polars, pyarrow) get this without downloading the dataset.

```python
import pyarrow.dataset as ds, pyarrow.compute as pc
d = ds.dataset("corpus_duplicates", format="parquet")
row = d.to_table(filter=pc.field("doc_id") == "shard_00877_14962")
```

#### What a row means

**Relationships are symmetric and both directions are present.** If A lists B, B lists A, so
filtering on `doc_id` alone gives a document's complete neighbourhood.

**`jaccard` is exact, not estimated.** MinHash was used only to *generate candidate pairs*;
every reported pair was then scored by computing the true Jaccard of the two documents'
5-gram word-shingle sets. Filtering to a stricter threshold is a predicate, not a re-run:

```python
strong = [n for n in row["near_duplicates"] if n["jaccard"] >= 0.9]
```

**`jaccard: 1.0` does not mean byte-identical.** It means identical after normalisation
(lowercased, non-alphanumerics collapsed) — two documents differing only in punctuation or
whitespace have the same shingle set. Byte-identical documents appear in `exact_duplicates`
instead. **The two lists are always disjoint.**

**Near duplication is not transitive.** A ≈ B and B ≈ C does not imply A ≈ C. This file
stores neighbourhoods rather than clusters, because any clustering loses information: a
partition must break some real pairs, and single-linkage merges documents never compared to
each other. Derive what you need — single-linkage is a BFS over these rows, complete-linkage
a clique cover.

#### What is deliberately absent

**Nothing below Jaccard 0.7.** Candidate recall falls to 43.6% at J = 0.5, so a lower
threshold would be substantially incomplete while appearing complete.

**About 0.2% of true pairs at the threshold.** LSH candidate recall is 99.81% at J = 0.7 and
effectively 100% above J = 0.8, so recall *rises* as you filter more strictly.

**One component of 91,408 documents.** Verification cost is quadratic in component size;
that component exceeded the cap and was excluded rather than clustered on weaker evidence.
Its sampled pairwise Jaccard is 0.000 median — unrelated documents chained by shared
boilerplate — so little real duplication is lost.

**Documents shorter than 5 words.** With 5-gram shingles they produce an empty shingle set
and cannot be compared this way. They still appear via `exact_duplicates`, which does not
depend on shingling.

#### Summary statistics

| | documents | share of corpus |
|---|---:|---:|
| no duplicates (no row) | 334,174,396 | 60.40% |
| any duplicate | 219,066,180 | 39.60% |
| — with a near duplicate (J ≥ 0.7) | 215,379,056 | 38.93% |
| — exact duplicates only | 3,687,124 | 0.67% |
| in an exact-duplicate group | 16,845,444 | 3.04% |

436,322,424 undirected near-duplicate pairs at J ≥ 0.7, across 5,910,028 exact-duplicate
groups. Duplication is overwhelmingly pairwise — 66% of documents with a near duplicate have
exactly one — though the largest exact group holds 6,377 byte-identical copies.

#### How it was built

Exact duplicates: SHA-256 over `text.strip()`, every group re-verified by direct byte
comparison. Near duplicates: MinHash over 512 permutations banded 73 × 7 (candidate recall
`1-(1-J^7)^73`), then exact Jaccard on 5-gram word shingles for every candidate pair.

Code, tests and full statistics: [`corpus_analysis/`](https://github.com/castorini/cmass/tree/main/corpus_analysis).

#### Verification

Group sizes were confirmed by an independent brute-force scan of the corpus sharing no code
with the pipeline — no SHA-256, no MinHash — which reproduced every sampled group exactly
and counted 553,240,576 documents, matching the corpus manifest. Separately, sampled records
were re-read from the corpus and re-scored with a reference Jaccard implementation: no
non-byte-identical exact pair, no Jaccard mismatch, no entry below threshold, no list
overlap.

These published files were checked against the pipeline output row for row: identical row
count (219,066,180) and an identical order-independent content checksum over every
`doc_id`, count, and `(neighbour, jaccard)` pair, plus per-file sortedness and range
coverage. Reproduce with `corpus_analysis/release/verify_release.py`.

#### Licence and citation

Inherits the licence of the underlying ClimbMix corpus. These files contain only document
identifiers and similarity scores — no document text. Cite CMASS alongside ClimbMix
(`arXiv:2504.13161`).
