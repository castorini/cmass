# CMASS

CMASS builds corpus-grounded agentic-search benchmarks by projecting existing question-answering
benchmarks onto a fixed retrieval corpus. Each question is decomposed into atomic reasoning hops,
and a question is retained only when every hop is supported by retrievable documents in the target
corpus.

The first release, **BrowseComp-Plus<sup>CM</sup>**, projects BrowseComp-Plus onto the 553-million-
document, 400-billion-token ClimbMix corpus. The released benchmark contains 57 human-verified
questions with question-level relevance judgments.

**[Download the dataset and qrels from Hugging Face](https://huggingface.co/datasets/castorini/cmass)**

## Projection Pipeline

The projection pipeline first determines which BrowseComp-Plus questions are answerable from
ClimbMix, then requires every reasoning hop to be grounded. An independent agent and the authors
verify the surviving questions before the supporting documents are pooled, filtered, and expanded
over duplicates to form the final qrels.

<img width="668" height="784" alt="Screenshot 2026-08-19 at 4 05 47 PM" src="https://github.com/user-attachments/assets/eeea1149-b197-41f8-8a83-22435b6727ee" />

Of the 830 source questions, 326 are answerable from ClimbMix and 65 pass automatic all-hop
verification. PIIKA answers all 65 correctly when given their supporting documents. Human review
then retains 57 questions for the released benchmark.

## PIIKA Results

The paper evaluates three PIIKA configurations on the 57 human-verified questions. In this
evaluation, agents receive no supplied documents and retrieve only through the corpus's BM25 index.
Accuracy and recall are percentages; tool calls are the average number of retrieval calls per question.

| Model | Corpus | Accuracy | Recall | Tool calls |
| --- | --- | ---: | ---: | ---: |
| GPT-5.6 Sol (max) | BrowseComp-Plus | 85.96 | 84.28 | 60.16 |
| GPT-5.6 Sol (max) | BrowseComp-Plus<sup>CM</sup> | 80.70 | 21.37 | 98.26 |
| Gemma 4 31B IT | BrowseComp-Plus | 26.32 | 24.91 | 24.46 |
| Gemma 4 31B IT | BrowseComp-Plus<sup>CM</sup> | 15.79 | 2.77 | 23.42 |
| Qwen 3.5 9B | BrowseComp-Plus | 14.04 | 19.33 | 33.44 |
| Qwen 3.5 9B | BrowseComp-Plus<sup>CM</sup> | 12.28 | 2.64 | 37.93 |

Projection makes retrieval substantially harder. For GPT-5.6 Sol, evidence recall falls from 84.28%
to 21.37% while the agent makes 63% more retrieval calls, even though answer accuracy decreases by
only about five points.

## Repository

- [`pipelines/bcp_climbmix/`](pipelines/bcp_climbmix/) contains the end-to-end
  BrowseComp-Plus-to-ClimbMix pipeline: projection, answerability checks, all-hop grounding,
  coverage audits, and independent-set audits. Its automatic output contains 65 fully grounded
  questions; the released benchmark is the human-verified set of 57.
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

## License

See [LICENSE](LICENSE).
