# CMASS

<h3 align="center">
ClimbMix Agentic Search Suite
</h3>

<p align="center">
|
<a href="https://arxiv.org/abs/2608.20317"><b>Paper</b></a> |
<a href="https://huggingface.co/datasets/castorini/cmass"><b>Dataset</b></a> |
<a href="pipelines/bcp_climbmix/README.md"><b>Projection</b></a> |
<a href="pipelines/bcp_climbmix/qrels/README.md"><b>Qrels</b></a> |
<a href="corpus_analysis/README.md"><b>Corpus Analysis</b></a> |
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2608.20317">
    <img alt="arXiv" src="https://img.shields.io/badge/arXiv-2608.20317-b31b1b.svg">
  </a>
  <a href="https://github.com/castorini/cmass/stargazers">
    <img alt="GitHub Stars" src="https://img.shields.io/github/stars/castorini/cmass?style=flat&logo=github&color=red">
  </a>
  <a href="https://huggingface.co/datasets/castorini/cmass">
    <img alt="Dataset" src="https://img.shields.io/badge/Hugging%20Face-CMASS-FFD21E">
  </a>
  <a href="https://github.com/castorini/cmass/blob/main/LICENSE">
    <img alt="License" src="https://img.shields.io/badge/License-Apache%202.0-blue.svg">
  </a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10+-blue.svg">
</p>

ClimbMix Agentic Search Suite (CMASS) builds corpus-grounded agentic-search
benchmarks by projecting existing question-answering benchmarks onto a fixed retrieval corpus. Each
question is decomposed into atomic reasoning hops, and a question is retained only when every hop is
supported by retrievable documents in the target corpus.

The first release, **BrowseComp-Plus<sub>CM</sub>**, projects BrowseComp-Plus onto the 553-million-
document, 400-billion-token ClimbMix corpus. The released benchmark contains 57 human-verified
questions with question-level relevance judgments.

## Quick Start

CMASS requires Python 3.10 or newer. Create an environment and install the dataset dependency:

```bash
git clone https://github.com/castorini/cmass.git
cd cmass
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Load and deobfuscate the BrowseComp-Plus<sub>CM</sub> queries and qrels:

```python
from datasets import load_dataset
from scripts.deobfuscate import decode_row

queries = load_dataset(
    "json",
    data_files="hf://datasets/castorini/cmass/bcp/queries.jsonl",
    split="train",
)
qrels = load_dataset(
    "json",
    data_files="hf://datasets/castorini/cmass/bcp/qrels.jsonl",
    split="train",
)

query = decode_row(queries[0])
decoded_qrels = (decode_row(row) for row in qrels)
query_qrels = [row for row in decoded_qrels if row["query_id"] == query["id"]]

print(query["question"])
print(query["answer"])
print([row["doc_id"] for row in query_qrels])
```

If the Hugging Face release requires authentication, run `hf auth login` first. See
[`pipelines/bcp_climbmix/`](pipelines/bcp_climbmix/) to reproduce the projection rather than only
consume the release.

## Projection Pipeline

The complete paper pipeline is shown below. **Stage 1: Projection** contains four operations, in
figure order: **Hop/Clue decomposition**, **Grounding**, **Answerability check**, and **All-hops
verification**. The first two are agent-driven; the latter two retain only corpus-answerable
questions whose full reasoning chains are grounded. **Stage 2: Independent agent validation with
PIIKA**, **Stage 3: Human verification**, and **Stage 4: Qrels construction** complete the pipeline.

<img width="668" height="784" alt="Projecting BrowseComp-Plus onto ClimbMix pipeline" src="https://github.com/user-attachments/assets/eeea1149-b197-41f8-8a83-22435b6727ee" />

Of the 830 source questions, 326 are answerable from ClimbMix and 65 pass automatic all-hops
verification. PIIKA answers all 65 correctly when given their supporting documents. Human review
then retains 57 questions for the released benchmark.

## PIIKA Results

The paper evaluates three PIIKA configurations on the 57 human-verified questions. In this
evaluation, agents receive no supplied documents and retrieve only through the corpus's BM25 index.
Accuracy and recall are percentages; tool calls are the average number of retrieval calls per question.

| Model | Corpus | Accuracy | Recall | Tool calls |
| --- | --- | ---: | ---: | ---: |
| GPT-5.6 Sol (max) | BrowseComp-Plus | 85.96 | 84.28 | 60.16 |
| GPT-5.6 Sol (max) | BrowseComp-Plus<sub>CM</sub> | 80.70 | 21.37 | 98.26 |
| Gemma 4 31B IT | BrowseComp-Plus | 26.32 | 24.91 | 24.46 |
| Gemma 4 31B IT | BrowseComp-Plus<sub>CM</sub> | 15.79 | 2.77 | 23.42 |
| Qwen 3.5 9B | BrowseComp-Plus | 14.04 | 19.33 | 33.44 |
| Qwen 3.5 9B | BrowseComp-Plus<sub>CM</sub> | 12.28 | 2.64 | 37.93 |

Projection makes retrieval substantially harder. For GPT-5.6 Sol, evidence recall falls from 84.28%
to 21.37% while the agent makes 63% more retrieval calls, even though answer accuracy decreases by
only about five points.

## Repository

- [`pipelines/bcp_climbmix/`](pipelines/bcp_climbmix/) contains the BrowseComp-Plus-to-ClimbMix
  implementation. Its top-level scripts implement **Stage 1: Projection**: **Hop/Clue
  decomposition**, **Grounding**, **Answerability check**, and **All-hops verification**. The
  [`qrels/`](pipelines/bcp_climbmix/qrels/) subdirectory implements **Stage 4: Qrels construction**.
  Stage 1 produces 65 fully grounded questions; subsequent validation and human review yield the
  released set of 57.
- [`corpus_analysis/`](corpus_analysis/) measures the ClimbMix corpus: token-length distribution,
  exact and near-duplicate detection, and how much of the corpus survives deduplication. Its
  per-document duplicate records are released as the `corpus_duplicates` config of the Hugging Face
  dataset; see [`corpus_analysis/release/`](corpus_analysis/release/) for how they are built and
  verified.
- [`scripts/deobfuscate.py`](scripts/deobfuscate.py) decodes the obfuscated `question`, `answer`, and
  `doc_id` fields in the Hugging Face release using each row's `canary`.
- [The Hugging Face release](https://huggingface.co/datasets/castorini/cmass) contains the benchmark
  queries and qrels, plus the `corpus_duplicates` config: exact and near-duplicate relationships for
  every duplicated ClimbMix document (219,066,180 rows). The corresponding documents come from the
  `climbmix-400b` corpus.

## Citation

If you use CMASS, please cite:

```bibtex
@misc{sharifymoghaddam2026projectingbrowsecompplusclimbmixrealistic,
      title={Projecting BrowseComp-Plus onto ClimbMix: Toward More Realistic Corpora for Agentic Search},
      author={Sahel Sharifymoghaddam and Lingwei Gu and Yijun Ge and Jimmy Lin},
      year={2026},
      eprint={2608.20317},
      archivePrefix={arXiv},
      primaryClass={cs.IR},
      url={https://arxiv.org/abs/2608.20317},
}
```

## License

See [LICENSE](LICENSE).
