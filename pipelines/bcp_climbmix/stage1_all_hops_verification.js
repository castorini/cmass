export const meta = {
  name: 'stage1-all-hops-verification',
  description: 'All-hops verification, one agent per question, two checks in one pass: (a) GROUNDING - every hop is stated by the full text of a ClimbMix document, verified by retrieval, verbatim snippet kept; (b) COVERAGE - every information-bearing content clue of the question (including numbers, counts, and intervals embedded in a phrase) is represented by a hop. Also marks redundant hops (kept, never dropped). keep = all hops grounded AND coverage ok. One output file per question; resumable.',
  phases: [ { title: 'All-hops verification', detail: 'per-hop full-doc grounding + question coverage; keep iff both' } ],
}

// qids come from Workflow args (array or JSON string). Inputs are per-question
// {qid, question, answer, hops:[clue strings]} files produced by
// stage1_all_hops_verification_build_inputs.py, from this pipeline's projection output or from any
// independent projection in the same schema.
const _A = (typeof args === "string" ? JSON.parse(args) : args);
const QIDS = _A && _A.length ? _A : [];
const ROOT = ".";                      // point at your working directory
const CM = ROOT + "/cm.py";
const INP = (q) => ROOT + "/inputs/" + q + ".json";
const OUT = (q) => ROOT + "/runs/" + q + ".json";

const SCHEMA = { type:"object", additionalProperties:false,
  required:["qid","all_supported","coverage_ok","keep"],
  properties:{
    qid:{type:"string"}, n_hops:{type:"integer"},
    all_supported:{type:"boolean"}, coverage_ok:{type:"boolean"}, keep:{type:"boolean"},
    hops:{type:"array", items:{type:"object", additionalProperties:false,
      required:["clue","supported"],
      properties:{ clue:{type:"string"}, supported:{type:"boolean"}, redundant:{type:"boolean"},
        doc_ids:{type:"array",items:{type:"string"}}, note:{type:"string"} }}},
    uncovered_phrases:{type:"array",items:{type:"string"}},
    reason:{type:"string"} } };

function prompt(q){ return [
"You VERIFY one BrowseComp-plus question for a strict corpus-grounded benchmark. qid="+q+". Tools: Read, Bash, Write.",
"A question QUALIFIES (keep=true) iff BOTH checks pass: (a) EVERY hop is grounded on ClimbMix, and (b) every information-bearing content clue of the question is represented by a hop. Hops are NEVER dropped to make a question pass.",
"",
"ACTION 0 - If "+OUT(q)+" already exists (Bash: test -f), READ it and return its exact JSON as StructuredOutput; do NOT redo the work.",
"",
"ACTION 1 - Read "+INP(q)+" : question, answer, and `hops` (the full decomposition into atomic clues).",
"",
"ACTION 2 - GROUNDING for EACH hop (the answer may NOT be assumed; ground the hop's own fact):",
"  python3 "+CM+" search \"<query naming the hop's entities + fact>\" [hits=30..200] [preview=600]",
"  python3 "+CM+" doc <docid>",
"  Issue several expanded queries (synonyms, aliases, paraphrases); escalate hits; read promising documents IN FULL.",
"  supported = true ONLY IF a specific ClimbMix document's own text states/establishes the hop's fact (copy a verbatim snippet into note); NOT merely topical, NOT from your own outside knowledge. Different documents may ground different hops.",
"  Temporal qualifiers: an exact 'as of <year>' is non-disqualifying when the entities and relations are grounded; only explicit in-text dates count as date evidence.",
"  Also set redundant = true when the hop's fact is already implied by the other hops (recorded for analysis; redundant hops still require grounding and are never removed).",
"",
"ACTION 3 - COVERAGE of the QUESTION (judge the hop list against the question text):",
"  Enumerate every information-bearing CONTENT phrase in the question: named entities, relations, specific events, superlatives, and quantities.",
"  CRITICAL - SPECIFIC NUMBERS: when the question states a specific number, count, or interval - e.g. 'married three times', 'between 24 and 25 years later', 'an attendance of 61,700' - that NUMBER is itself a content sub-clue; a hop mentioning the surrounding event but omitting the number does NOT cover it.",
"  IGNORE 'as of <year>' timestamp qualifiers - those are non-blocking.",
"  Any content phrase with no corresponding hop goes into uncovered_phrases.",
"",
"ACTION 4 - Aggregate and WRITE "+OUT(q)+" :",
"  all_supported = every hop supported; coverage_ok = uncovered_phrases empty; keep = all_supported AND coverage_ok.",
"  File JSON = {qid, n_hops, all_supported, coverage_ok, keep, hops:[{clue, supported, redundant, doc_ids, note}], uncovered_phrases, reason}",
"Return StructuredOutput with the same fields.",
].join("\n"); }

if (!QIDS.length) { log("No qids passed via args - nothing to do."); return { judged: 0 }; }
log("All-hops verification: "+QIDS.length+" questions.");
const rows = (await pipeline(QIDS,
  (q) => agent(prompt(q), { label:"verify:"+q, phase:"Verify", schema:SCHEMA, agentType:"general-purpose" })
)).filter(Boolean);
const keep = rows.filter(r=>r.keep).map(r=>r.qid).sort();
log("verified="+rows.length+" keep="+keep.length);
return { judged:rows.length, keep:keep.length, keep_qids:keep,
  rows:rows.map(r=>({qid:r.qid, keep:r.keep, all_supported:r.all_supported, coverage_ok:r.coverage_ok,
    uncovered:(r.uncovered_phrases||[]).slice(0,3)})) };
