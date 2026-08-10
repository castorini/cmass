export const meta = {
  name: 'stage2-hop-scrutiny',
  description: 'For each PROJECTABLE question, independently scrutinize EVERY hop: (1) is it corpus-supported on ClimbMix (agentic search + query expansion), and (2) is it necessary (needed to uniquely pin the answer)? Keep only questions where all hops are supported AND all hops are necessary (no unsupported, no redundant).',
  phases: [ { title: 'Scrutinize', detail: 'per-hop support (ClimbMix) + necessity; KEEP iff all supported & all necessary' } ],
}

// ===== SET PER CHUNK =====
const QIDS = ["1015","1039","1040","1043","1045","1049","1052","1058","1061","1065","1068","1076","1077","1078","1083","1091","1093","1094","1095","1101","1108","1128","1131","1134","1149","1152","1155","1158","1162","1164","1172","1174","1176","1177","1179","1182","1184","1187","1190","1191","1192","1193","1194","1195","1198","1200","1207","1210","1213","1214","1215","1216","1217","1218","1219","1220","1221","1223","1225","1226","1227","1228","1231","1237","1238","1239","1246","1247","1248","1250","1252","1253","1257","1258","1262","1264","1265"];
// =========================

const ROOT = "/Users/lingweigu/Research/agent-plus/artifacts/bcp_stage1";
const CM = ROOT + "/q_all/tools/cm.py";
const INP = (q) => ROOT + "/stage2/inputs/" + q + ".json";
const OUT = (q) => ROOT + "/stage2/runs/" + q + ".json";

const SCHEMA = { type:"object", additionalProperties:false,
  required:["qid","n_hops","all_supported","all_necessary","keep"],
  properties:{
    qid:{type:"string"}, n_hops:{type:"integer"},
    n_supported:{type:"integer"}, n_necessary:{type:"integer"},
    all_supported:{type:"boolean"}, all_necessary:{type:"boolean"}, keep:{type:"boolean"},
    hops:{type:"array", items:{type:"object", additionalProperties:false,
      required:["clue","supported","necessary"],
      properties:{ clue:{type:"string"}, supported:{type:"boolean"}, necessary:{type:"boolean"},
        doc_ids:{type:"array",items:{type:"string"}} }}} } };

function prompt(q){return [
"You SCRUTINIZE the hops of a BrowseComp-plus question to decide if it qualifies for a strict benchmark: EVERY hop must be (a) supported on the ClimbMix corpus and (b) necessary. qid="+q+". Tools: Read, Bash, Write.",
"",
"STEP 1 - Read "+INP(q)+" : question, answer, and `hops` (the full decomposition into atomic clues).",
"",
"STEP 2 - For EACH hop, check SUPPORT on ClimbMix (the answer may NOT be assumed; ground the hop's fact):",
"  python3 "+CM+" search \"<query naming the hop's entities + fact>\" [hits=30..200] [preview=600]",
"  python3 "+CM+" doc <docid>",
"  Issue several expanded queries (synonyms, aliases, paraphrases); escalate hits; read promising docs in full.",
"  supported = true ONLY IF a specific ClimbMix doc states/establishes the hop's fact (copy a verbatim snippet); NOT merely topical, NOT from your own outside knowledge.",
"",
"STEP 3 - For EACH hop, check NECESSITY: would the answer still be UNIQUELY determined by the OTHER hops if this hop were removed?",
"  necessary = true if removing the hop would let a different entity/value satisfy the remaining hops (i.e., it adds discriminating power).",
"  necessary = false (REDUNDANT) if the remaining hops already uniquely pin the answer without it.",
"",
"STEP 4 - Aggregate: all_supported = every hop supported; all_necessary = every hop necessary; keep = all_supported AND all_necessary (no unsupported hop, no redundant hop).",
"",
"STEP 5 - WRITE "+OUT(q)+" = {qid, n_hops, n_supported, n_necessary, all_supported, all_necessary, keep, hops:[{clue, supported, doc_ids:[...], necessary, note}]}",
"Return StructuredOutput {qid, n_hops, n_supported, n_necessary, all_supported, all_necessary, keep, hops}.",
].join("\n");}

log("Stage-2 hop scrutiny: "+QIDS.length+" PROJECTABLE questions (per-hop support + necessity).");
const rows = await pipeline(
  QIDS,
  (q) => agent(prompt(q), { label:"scrut:"+q, phase:"Scrutinize", schema:SCHEMA, agentType:"general-purpose" })
);
const c = rows.filter(Boolean);
const keep = c.filter(r=>r.keep).map(r=>r.qid).sort();
const allsup = c.filter(r=>r.all_supported).map(r=>r.qid).sort();
log("scrutiny chunk: judged="+c.length+" all_supported="+allsup.length+" KEEP(all supported & necessary)="+keep.length);
return { judged:c.length, all_supported:allsup.length, keep:keep.length, keep_qids:keep,
         rows:c.map(r=>({qid:r.qid, all_supported:r.all_supported, all_necessary:r.all_necessary, keep:r.keep, n_hops:r.n_hops, n_supported:r.n_supported, n_necessary:r.n_necessary})) };
