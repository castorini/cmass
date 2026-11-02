"""CMASS projection pipeline: shared code for Stage 2 (audit, revision, human review) and Stage 3 (qrels).

Every tool works on one run directory (see run.py): the projected candidates from Stage 1 go in, and
each stage writes its per-question records beside them, so every step can be inspected and resumed.
Nothing here is specific to one corpus or dataset: the corpus is reached through a configurable
search / get_document backend (corpus.py), and dataset rules come from the run's config (config.py).
"""
