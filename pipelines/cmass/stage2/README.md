# Stage 2: audit, revision, adversarial check, human review and final check

Stage 2 takes the candidates Stage 1 projected (and its near misses), checks each one against the
corpus, repairs what its dataset's rules allow, challenges what passes, has a person accept or reject
it, gives the accepted ones a last check, and writes the records Stage 3 builds qrels from. It follows
the paper's Stage 2 (Section "Methodology") and its appendix "Audit, Revision, and Human Review"
(steps C1-C6). Nothing here is specific to one dataset or corpus: the corpus is reached through the
run's configured backend (`cmass/corpus.py`), and each set's revision limits come from its dataset
family (`cmass/config.py`).

Every step writes one file per question in the run directory (`cmass/run.py`), and
`tools/stage2_state.py` derives each question's next step from those files alone: the workflows run
it before and after every agent, so they resume from disk and never trust an agent's account of its
own work. Record formats: [`SCHEMA.md`](SCHEMA.md).

## Figure-to-code map

| step | implementation | what happens |
|---|---|---|
| **C1** inputs and evidence | `cmass/stage2.py` (`candidate_problems`), `tools/packets.py`, `tools/cm.py` | The candidate record is checked (question, answer, answer type, hops, cited documents); a malformed one is excluded. `packets.py` writes the auditor's packet: each hop's cited documents with whether its snippet occurs in them and a context window. Agents search, read, quote and verify through `cm.py`, which logs every query to the question's search log. |
| **C2** constraint and hop audit | `audit.js` (auditor), [`AUDIT.md`](AUDIT.md), `tools/validate.py` | Constraints as verbatim spans of the question; hops typed required / confirmatory / derivation with roles inclusion / exclusion; every factual hop grounded by quotes read in context; rivals and set members searched by three routes at depth 50. |
| **C3** answer accuracy and completeness | `audit.js` (auditor, then an independent second reader), [`AUDIT.md`](AUDIT.md), `tools/validate.py` | Candidates qualify or are excluded by grounded facts; completeness by a list document, a fixed domain or a recorded three-way search; contamination; verdict pass / pass with fixes / fail with the failure reasons. `validate.py` must report 0 errors. |
| **C4** permitted revision | `revision.js`, [`REVISION.md`](REVISION.md), `tools/check_records.py` | One repair attempt per trigger (failed audit, verified refutation, accept with fixes, failed final check): evidence retry first, then the least extensive edit the set's family permits. The repaired version is re-audited (C1-C3) and re-challenged (C5). |
| **C5** adversarial verification | `adversarial.js`, [`ADVERSARIAL.md`](ADVERSARIAL.md), `tools/check_records.py` | A hunter (two, by list documents and by named candidates, for aggregates and changed answers) looks for rivals, missing or wrong members, conflicting values and alternative readings; a verifier rules on every finding. A verified refutation permits one repair; a second excludes. |
| **C5** human review | `tools/assemble.py`, `review_site/` | The audited records go to the review site in its record format, revised versions beside their source wording; the reviewer accepts, accepts with fixes (back to C4) or rejects. A decision binds only the version it was made on. |
| **C6** final check | `final_check.js`, [`FINAL_CHECK.md`](FINAL_CHECK.md), `tools/check_records.py` | For sets whose config has `final_check` true (bcp, trqa, dsqa, generic families): a fresh check of the accepted version; a repairable gap goes back to C4 once. hotpotqa is exempt. |
| **C6** output records | `tools/finalize.py` | Quotes are matched again (a mismatch stops finalization), records are checked, duplicates from several input lists are kept once, and `final/<set>_final.jsonl` and `final/<set>_excluded.jsonl` are written. |

`cmass/stage2.py` holds the state logic every tool shares; `tools/stage2_state.py` prints it.

## The run directory

Create a run directory **outside this repository** (it holds benchmark content) with `cmass.json`
(see `cmass/config.py`) and the Stage 1 output under `inputs/`:

```json
{"corpus": {"name": "climbmix-400b", "backend": "rest",
            "rest": {"base_url": "https://example.org", "index": "climbmix-400b", "token_env": "PYSERINI_API_TOKEN"}},
 "sets": {"set_a":    {"input": "inputs/set_a.jsonl", "family": "dsqa", "group": "released"},
          "nm_set_a": {"input": "inputs/set_a_near_misses.jsonl", "family": "dsqa", "group": "near_miss"}}}
```

A set may override any family field, e.g. `"final_check": false`, `"two_hunters": true`,
`"max_added_qualifiers": 1`, `"title"`, or a `"canary"` shown on the review site. Name near-miss sets
`nm_<name>`: the review site shows its salvage view for tabs whose id starts with `nm_` or `rw_`.
Every tool runs inside the run directory (or with `$CMASS_RUN` set).

## Commands, in order

`TOOLS` is `pipelines/cmass/tools`. The `.js` files are Claude Code Workflow scripts: set `RUN` and
`TOOLS` at the top of each (absolute paths), or pass `{"run": ..., "tools": ...}` in the Workflow
args. Args name the questions: `{"set": "set_a"}` takes every question of the set whose next step
the script handles, `{"set": "set_a", "qids": ["17", "42"]}` some of them, `{"items": [{"set", "qid"}, ...]}`
several sets. Check progress at any time with `python3 TOOLS/stage2_state.py`.

1. **Audit (C1-C3).** Run `audit.js` per set. Each question gets an auditor (writes
   `work/<set>/<qid>.audit.json`, `validate.py` to 0 errors) and then an independent second reader.
   Questions that pass go on to `hunt`; failures go to `revise`; contaminated ones are excluded.
2. **Repair the failures (C4).** Run `revision.js` (questions whose next step is `revise`). It writes
   one attempt per question, re-audits the repaired version (`audit.js`) and challenges it
   (`adversarial.js`).
3. **Adversarial check (C5).** Run `adversarial.js` for the questions whose next step is `hunt` or
   `verify`. A verified refutation sends the question to `revise`: run `revision.js` again.
4. **Human review (C5).**
   ```bash
   python3 TOOLS/assemble.py          # audited/*.jsonl, review/docs.jsonl, review/site_config.json, review/index.html
   python3 review_site/build_site_data.py <run>/review/site_config.json --out <run>/review/data
   python3 review_site/serve.py --site <run>/review      # http://127.0.0.1:8765/
   ```
   The site has one tab per set (groups released and near misses, as configured) and a
   "revised versions" tab `rw_<set>` per set whose questions were reworded or got a new answer; each
   such question also stays in its home tab "as written", pointing to its revision. Questions whose
   next step is `review` are ready; the note under each question gives its Stage 2 state, candidate
   decisions, completeness and the hunters' findings with the verifier's rulings. Decisions are saved
   to `<run>/review/reviews/reviews.json` (keys `<tab>~<record id>`; `review_site/README.md`). A
   revised version has its own record id (`<qid>.r<N>`), so an earlier approval never carries over.
   - **accept**: on to the final check (or straight to output for exempt sets);
   - **accept with fixes**: the comments go to `revision.js`; rebuild the site and review the repaired
     version;
   - **reject**: excluded.

   When a decision changes a general audit rule, reopen the questions it affects:
   `python3 TOOLS/stage2_state.py --reopen SET QID ... --note "the rule"`, then rerun `audit.js`.
5. **Final check (C6).** Run `final_check.js`. A repairable gap goes to `revise` once
   (`revision.js`, then review again, then `final_check.js` again); a second failure excludes.
6. **Output.** `python3 TOOLS/finalize.py` writes `final/<set>_final.jsonl` and
   `final/<set>_excluded.jsonl` once every question is decided (`--partial` writes what is decided and
   lists the rest). These records are the input of Stage 3 (`SCHEMA.md`, "Output records").

`python3 TOOLS/check_records.py` checks every revision, adversarial and final-check file against
`SCHEMA.md` and the set's permitted revisions at any time; `validate.py` checks the audits.

## Try it offline

`demo/` holds an invented corpus (the island nation of Oskeria) and a script that writes a run with
Stage 2 records covering every path above (no agents needed):

```bash
python3 pipelines/cmass/demo/make_stage2_demo.py
cd pipelines/cmass/demo/run
python3 ../../tools/validate.py && python3 ../../tools/check_records.py
python3 ../../tools/stage2_state.py
python3 ../../tools/packets.py
python3 ../../tools/assemble.py
python3 ../../../../review_site/build_site_data.py review/site_config.json --out review/data
python3 ../../../../review_site/serve.py --site review
python3 ../../tools/finalize.py --partial      # one demo question is left waiting for its adversarial check
```

## Reproducibility

The agent steps are not bit-for-bit reproducible; their per-question files are the canonical record,
and `assemble.py` and `finalize.py` rebuild the review data and the output deterministically from them.
Rerunning the agentic work from scratch needs only the corpus backend and reproduces an equivalent,
not identical, set. The search-based completeness judgment is a recorded best effort, not proof that
every corpus document was checked.
