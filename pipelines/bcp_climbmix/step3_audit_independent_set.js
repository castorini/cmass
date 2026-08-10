export const meta = {
  name: 'independent-set-audit',
  description: 'Audit Sahel projections (65 candidates in the 326, not in our 50 final): (1) all hops present and lead question->answer; (2) each hop grounded by >=1 cited ClimbMix doc whose RETRIEVED CONTENT actually supports it (doc-id presence is not enough). KEEP iff both.',
  phases: [ { title: 'Audit', detail: 'coverage/chain + retrieval-verified grounding' } ],
}
const QIDS = ["51", "62", "78", "81", "109", "156", "175", "176", "226", "231", "237", "241", "246", "248", "257", "285", "298", "299", "328", "342", "450", "470", "473", "491", "503", "520", "558", "570", "588", "592", "607", "620", "621", "633", "637", "672", "706", "719", "726", "797", "809", "814", "826", "840", "856", "907", "916", "919", "920", "927", "932", "961", "966", "972", "979", "1005", "1016", "1052", "1101", "1172", "1176", "1177", "1179", "1198", "1214"];
const CM = "/Users/lingweigu/Research/agent-plus-stage2/artifacts/bcp_stage1/q_all/tools/cm.py";
const INP = (q) => "/Users/lingweigu/Research/agent-plus-stage2/artifacts/bcp_stage1/stage2/sahel_audit/inputs/" + q + ".json";
const OUT = (q) => "/Users/lingweigu/Research/agent-plus-stage2/artifacts/bcp_stage1/stage2/sahel_audit/runs/" + q + ".json";
const SCHEMA = { type:"object", additionalProperties:false,
  required:["qid","all_hops_present","chain_ok","all_hops_grounded","keep"],
  properties:{ qid:{type:"string"}, all_hops_present:{type:"boolean"}, chain_ok:{type:"boolean"},
    all_hops_grounded:{type:"boolean"}, keep:{type:"boolean"}, reason:{type:"string"},
    per_hop:{type:"array", items:{type:"object", additionalProperties:false, required:["clue","grounded"],
      properties:{ clue:{type:"string"}, grounded:{type:"boolean"}, doc_used:{type:"string"}, why:{type:"string"} }}} } };
function prompt(q){ return [
"You AUDIT Sahel's projection of one BrowseComp-plus question for a strict corpus-grounded benchmark. qid="+q+". Tools: Read, Bash.",
"",
"STEP 0 - If "+OUT(q)+" exists (Bash test -f), read it and return its JSON as StructuredOutput; do not redo.",
"STEP 1 - Read "+INP(q)+" : {question, answer, hops:[{clue, corpus_doc_ids, sahel_snippets}]}.",
"",
"STEP 2 - COVERAGE + CHAIN: judge whether the hops together (a) cover the question's stated constraints and (b) form a reasoning chain that leads from the question to the given answer. all_hops_present=true iff no necessary step is missing; chain_ok=true iff the hops logically pin the answer.",
"",
"STEP 3 - GROUNDING (RETRIEVE, do not trust doc-ids): for EACH hop, fetch its cited ClimbMix doc(s):",
"    python3 "+CM+" doc <doc_id>",
"  Read the returned doc CONTENT and decide if it actually states/supports the hop's clue (same entities and fact; NOT merely on-topic, NOT a different person/place, NOT only a keyword match). A hop is grounded=true iff at least one cited doc's retrieved content genuinely supports it. Record doc_used (the supporting doc id, or empty) and a one-line why. Presence of a doc_id is NOT sufficient.",
"",
"STEP 4 - all_hops_grounded = every hop grounded. keep = all_hops_present AND chain_ok AND all_hops_grounded.",
"  WRITE "+OUT(q)+" = {qid, all_hops_present, chain_ok, all_hops_grounded, keep, reason, per_hop:[{clue, grounded, doc_used, why}]}.",
"Return StructuredOutput with the same fields.",
].join("\n"); }
log("Sahel all-hop audit (retrieval-verified): "+QIDS.length+" candidates.");
const rows=(await pipeline(QIDS,(q)=>agent(prompt(q),{label:"saudit:"+q,phase:"Audit",schema:SCHEMA,agentType:"general-purpose"}))).filter(Boolean);
const keep=rows.filter(r=>r.keep).map(r=>r.qid).sort();
log("audit: judged="+rows.length+" KEEP="+keep.length);
return { judged:rows.length, keep:keep.length, keep_qids:keep,
  rows:rows.map(r=>({qid:r.qid, keep:r.keep, present:r.all_hops_present, chain:r.chain_ok, grounded:r.all_hops_grounded,
    ungrounded:(r.per_hop||[]).filter(h=>!h.grounded).map(h=>({clue:h.clue.slice(0,50),why:(h.why||"").slice(0,90)})), reason:r.reason||""})) };
