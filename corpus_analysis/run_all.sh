#!/usr/bin/env bash
# Full corpus analysis, in dependency order.
#
#   CORPUS_DIR=/path/to/corpus CORPUS_OUT_DIR=/path/to/artifacts bash run_all.sh
#
# Every step is resumable, so re-running after an interruption picks up where it stopped.
# Runtimes below are measured on 96 cores over ClimbMix (553M documents, 600 GB of parquet);
# the whole thing is ~23 h and ~2.5 TB at peak. Run with DRY=1 to print the plan instead.
set -euo pipefail
cd "$(dirname "$0")"

DRY=${DRY:-0}
W=${WORKERS:-48}
TOKENIZER=${TOKENIZER:-llama2}
run() { echo; echo "=== $* ==="; if [ "$DRY" = "1" ]; then python "$@" --dry-run; else python "$@"; fi; }

# --- exact duplicates -------------------------------------------------  ~1.5 h,  72 GB
run exact/step0_manifest.py
run exact/step1_scan.py       --workers "$W"     # 20 min
run exact/step2a_length_map.py                   # 4 min   (optional cross-check)
run exact/step2b_hash_map.py                     # 20 min
run exact/step3_verify.py     --workers "$W"     # 29 min
run exact/step4_summary.py                       # 22 min

# --- near duplicates --------------------------------------------------  ~13 h,  2.2 TB
run near/step0_representatives.py
run near/step1_signatures.py  --workers "$W"     # ~3 h    1.0 TB of signatures
run near/step2_band.py        --workers "$W"     # 69 min  456 GB of band keys
run near/step3_components.py                     # 4.9 h   needs ~139 GB RAM
run near/step4_cliques.py     --workers "$W" --keep-buckets   # 1.9 h scatter + 2.2 h cliques
run near/step5_doc_records.py --workers 16       # 69 min  -> doc_duplicates_t70/ (4.4 GB)
run near/step6_summary.py

# --- token lengths ----------------------------------------------------  ~6 h
run tokens/step0_tokenize.py  --workers "$W" --tokenizer "$TOKENIZER"   # 6.2 h for Llama-2
run tokens/step1_length_stats.py             --tokenizer "$TOKENIZER"

# --- analysis + figures -----------------------------------------------  < 1 h
run analysis/duplicate_stats.py
run analysis/duplicate_composition.py
run analysis/clique_retention.py             --tokenizer "$TOKENIZER"
run analysis/dup_vs_length.py                --tokenizer "$TOKENIZER"
run figures/make_figures.py                  --tokenizer "$TOKENIZER"

# --- verification (cheap; run it before quoting any number) -----------
run verify/check_cliques.py
run verify/sample_records.py

echo
echo "done. figures in figures/out/, summaries in the analysis output directory."
