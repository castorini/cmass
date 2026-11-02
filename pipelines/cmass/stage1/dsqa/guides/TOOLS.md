# Tools for the DSQA Stage 1 agents

Every DSQA Stage 1 agent works on one question. Its input is a JSON packet
(`stage1/<set>/packets/<step>/<qid>.json`); its output is one JSON file
(`stage1/<set>/<step>/<qid>.json`). The workflow gives you both paths, the set and the qid. Run every command
from the run directory. `TOOLS` is `pipelines/cmass/tools`.

## The corpus

The original agents had a two-command client, `cm.py search "<query>" [hits] [preview_chars]` and
`cm.py doc <docid>`, over the corpus's BM25 index. Use the shared corpus CLI instead, always with
`--stage 1 --step STEP --set SET --qid QID`, so every search is logged for the question in this step's own log:

| original command | use |
|---|---|
| `cm.py search "<query>" 30 600` (defaults: 30 hits, 600-character previews) | `python3 TOOLS/cm.py search --stage 1 --step STEP --set SET --qid QID "<query>" --hits 30 --chars 600 --center` |
| `cm.py search "<query>" 40 300` (the later steps' rescue searches) | `... search --stage 1 --step STEP --set SET --qid QID "<query>" --hits 40 --chars 300 --center` |
| `cm.py doc <docid>` (the whole document) | `python3 TOOLS/cm.py doc --stage 1 --step STEP --set SET --qid QID DOCID --max 1000000` |

Differences to know:

- `--center` centres each preview on the densest window of the query's words, as the original client did;
  `--grep "<phrase>"` centres it on a phrase instead. Read any promising document in full before you judge it.
- `cm.py doc` prints only the first 6,000 characters unless you pass `--max`: pass `--max 1000000` to read a
  document in full, which every step requires. `--grep "<phrase>"` prints the text around a phrase.
- `cm.py search` refuses a query too similar to one already run for the question in this step
  (`python3 TOOLS/cm.py history --stage 1 --step STEP --set SET --qid QID` lists them); the original client
  had no such check. Change the words, or pass `--force` to rerun a query on purpose (for example deeper).
- `cm.py verify DOCID "<snippet>"` says whether a snippet occurs verbatim in a document. The original
  agents did not have it; you may use it to check a snippet you copied. The deterministic steps check
  every snippet anyway.
- BM25 takes keywords. A failed request (an HTTP error, a timeout) says nothing about the corpus: wait and
  run it again. The corpus service is shared: run one command at a time.

## Evidence

- Only corpus documents ground facts. Your own knowledge, the gold answer and any web page never count
  as evidence; they may suggest what to search for.
- A snippet is copied **verbatim** from the document it is attributed to: you may trim it and normalise
  whitespace, nothing else. Fragments of one document may be joined with ` ... `; never join text from
  two documents.
- Write your output as one JSON object to the output path, with exactly the fields your step's guide
  lists, then run the check the workflow names (`dsqa_state.py --set SET STEP QID`) and fix every error it
  prints until it reports `done`.
- If you cannot finish, say so (completed false, with the reason). Never claim success for work you did
  not complete: the files on disk are checked afterwards.
