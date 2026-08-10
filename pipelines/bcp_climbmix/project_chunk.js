export const meta = {
  name: 'bcp-all-project-chunk',
  description: 'Resumable inference-allowed projection of a chunk of BCP records: 1 agent/record decomposes -> match-centered ClimbMix retrieval -> PROJECTABLE/PARTIAL/NOT, and WRITES its own projections/bcp_<qid>.json (so budget cut-offs lose no completed work).',
  phases: [ { title: 'Project', detail: '1 agent per record; self-contained inference-allowed projection; writes own output file' } ],
}

// ===== SET PER CHUNK: the record_ids to project this run =====
// Resumable: a record is DONE iff q_all/projections/bcp_<qid>.json exists.
// To project the whole set, compute remaining = (all task ids) - (already-projected) and
// run in chunks of ~30-100 (see REPRODUCE.md). Example: const QIDS = ["1","3","5", ...];
const QIDS = [];
// =============================================================

const RUN = "/Users/lingweigu/Research/agent-plus/artifacts/bcp_stage1/q_all";
const CM = RUN + "/tools/cm.py";
const taskPath = (q) => RUN + "/subagent_tasks/bcp_" + q + ".task.json";
const outPath = (q) => RUN + "/projections/bcp_" + q + ".json";

const SCHEMA = {
  type: "object", additionalProperties: false,
  required: ["record_id", "verdict", "wrote_file"],
  properties: {
    record_id: { type: "string" },
    verdict: { type: "string", enum: ["PROJECTABLE", "PARTIAL", "NOT"] },
    n_hops: { type: "integer" },
    n_supported_hops: { type: "integer" },
    wrote_file: { type: "boolean" },
    note: { type: "string" },
  },
};

function prompt(qid) {
  return [
"You are an inference-allowed PROJECTOR for BrowseComp-plus -> ClimbMix. qid = " + qid + ". You have Bash, Read, Write.",
"GOAL: decide whether THIS question's answer can be UNIQUELY reasoned out from the ClimbMix corpus, and record the grounded reasoning.",
"",
"STEP 1 - Read the task: " + taskPath(qid) + " (record_id, question, answer, provided_docs = original gold/qrel docs, as HINTS for what the reasoning steps are).",
"",
"STEP 2 - Decompose the question into the minimal HOPS (atomic facts) on the path from the question's constraints to the answer (use provided_docs to see what the steps are; aim 4-6).",
"",
"STEP 3 - For each hop, retrieve from ClimbMix and judge support (INFERENCE ALLOWED - a hop counts if stated in OR soundly inferable from corpus docs: multi-doc synthesis, unit/temporal/arithmetic conversion, narrative deduction, entity linking). Previews are CENTERED ON THE QUERY MATCH:",
"  python3 " + CM + " search \"<nl query naming the answer entity + the fact>\" [hits=30] [preview=600]",
"  python3 " + CM + " doc <docid>",
"Issue >=3-6 varied queries per hop; escalate hits 30->100->200 for hard hops; copy a VERBATIM corpus snippet for each supported hop.",
"",
"STEP 4 - Decide the VERDICT using the UNIQUENESS test (the answer may itself be reasoned/computed, need not be verbatim):",
"  PROJECTABLE = a corpus-only reasoning agent can UNIQUELY derive THIS answer (the corpus-supported hops together pin the answer and rule out other entities). NOTE: the question may be over-constrained - a DISCRIMINATING SUBSET of hops that is corpus-supported is sufficient; unsupported NON-discriminating hops do not disqualify.",
"  PARTIAL = the answer is reachable but NOT uniquely pinned from corpus (other entities could fit the supported facts), or a decisive identifying step is weak.",
"  NOT = a decisive identifying fact is genuinely ABSENT from ClimbMix or the corpus CONTRADICTS the answer.",
"",
"STEP 5 - WRITE your result (Write tool) to: " + outPath(qid),
"{ \"record_id\":\"" + qid + "\", \"question\":\"<q>\", \"answer\":\"<a>\", \"verdict\":\"PROJECTABLE|PARTIAL|NOT\",",
"  \"answer_uniquely_inferable\": true|false,",
"  \"discriminating_hops\": [ {\"clue\":\"<hop>\", \"corpus_evidence\":[{\"doc_id\":\"<shard_...>\",\"snippet\":\"<verbatim>\"}]} ],",
"  \"redundant_or_unsupported_hops\": [ {\"clue\":\"<hop>\", \"status\":\"redundant|unsupported\"} ],",
"  \"reasoning_path\": \"<how corpus facts -> the answer>\", \"rationale\": \"<concise>\" }",
"",
"STEP 6 - return StructuredOutput: record_id, verdict, n_hops, n_supported_hops, wrote_file=true, note (one line).",
"IMPORTANT: always WRITE the output file before returning - it is the resumable progress record.",
  ].join("\n");
}

log("BCP-all projection chunk: " + QIDS.length + " records.");
const rows = await pipeline(
  QIDS,
  (qid) => agent(prompt(qid), { label: "proj:" + qid, phase: "Project", schema: SCHEMA, agentType: "general-purpose" })
);
const clean = rows.filter(Boolean);
const tally = (x) => clean.filter(r => r.verdict === x).map(r => r.record_id).sort();
log("chunk done: PROJECTABLE=" + tally("PROJECTABLE").length + " PARTIAL=" + tally("PARTIAL").length + " NOT=" + tally("NOT").length);
return { done: clean.length, PROJECTABLE: tally("PROJECTABLE"), PARTIAL: tally("PARTIAL"), NOT: tally("NOT"),
         rows: clean.map(r => ({qid:r.record_id, verdict:r.verdict, wrote_file:r.wrote_file})) };
