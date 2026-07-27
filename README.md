# CMASS

CMASS releases corpus-grounded deep-research benchmarks and the pipelines used
to construct them. The first release maps a verified subset of
[BrowseComp-Plus](https://github.com/texttron/BrowseComp-Plus) onto the fixed
ClimbMix-400B corpus.

## BCP -> ClimbMix v1

| | |
| --- | ---: |
| Questions | 65 |
| Grounded hops | 400 |
| Question-document qrel pairs | 7,270 |
| Distinct relevant ClimbMix documents | 7,242 |
| Reviewer evidence excerpts | 721 |

- [Dataset and schemas](data/bcp/README.md)
- [Projection and qrel pipeline](pipelines/bcp/README.md)
- [Human-readable evidence review](data/bcp/evidence_review.md)
- [Interactive dataset explorer](https://castorini.github.io/cmass/)

The canonical release files are:

```text
data/bcp/questions.jsonl
data/bcp/qrels.jsonl
data/bcp/qrels_hops.jsonl
data/bcp/qrels.trec
```

`questions.jsonl` contains the 65 all-hop-grounded projections. For retrieval
evaluation, use `qrels.jsonl` or the standard four-column `qrels.trec`. The hop
decomposition and evidence review are transparency artifacts and should not be
shown to an agent under evaluation.

## Validate the release

The website payload and TREC qrels are generated from the canonical JSONL
files. Rebuild and validate them with:

```bash
python3 scripts/build_release_assets.py
python3 scripts/validate_release.py
```

## Provenance

The questions originate from the MIT-licensed
[`Tevatron/browsecomp-plus`](https://huggingface.co/datasets/Tevatron/browsecomp-plus)
dataset. ClimbMix is introduced in
[CLIMB](https://arxiv.org/abs/2504.13161) and is used as the fixed retrieval
corpus. This repository releases short review excerpts and ClimbMix document
identifiers, not the full corpus.

The agentic projection and qrel-expansion stages are nondeterministic. The
committed data files are the canonical reviewed outputs; rerunning the pipeline
recreates the methodology, not necessarily byte-identical selections.

## License

Code in this repository is licensed under Apache-2.0. Upstream datasets and
corpora remain subject to their respective licenses and terms. See [NOTICE](NOTICE).
