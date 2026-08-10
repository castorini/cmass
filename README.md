# cmass

Corpus-grounded benchmark construction: projecting existing QA benchmarks onto a fixed retrieval
corpus so that every question's full reasoning chain is supported by retrievable documents.

- `pipelines/bcp_climbmix/` - the end-to-end BrowseComp-plus -> ClimbMix pipeline (projection,
  answerability, all-hops grounding, coverage audit, independent-set audit). Its output is the
  65-question all-hops-grounded set; the released benchmark is the human-verified 57.
- `scripts/deobfuscate.py` - decodes the obfuscated `question`, `answer`, and `doc_id` fields of the
  Hugging Face release (base64 + XOR with a keystream derived from each row's `canary`).

The released dataset (queries + qrels) lives on Hugging Face; documents come from the ClimbMix corpus
(`climbmix-400b`).
