export const meta = {
  name: 'hop-grounding-pass2',
  description: 'Re-scrutinize each PROJECTABLE question with THREE checks: (STEP2) per-hop ClimbMix grounding, (STEP3) per-hop necessity, and NEW (STEP4) COMPREHENSIVENESS / no-missing-hop (do the necessary grounded hops JOINTLY & UNIQUELY determine the answer using only the corpus?). KEEP iff every necessary hop supported AND comprehensive. Writes runs_v2/ (canonical runs/ preserved).',
  phases: [ { title: 'Scrutinize+', detail: 'grounding + necessity + comprehensiveness; KEEP iff all-necessary-supported & comprehensive' } ],
}

// ===== SET PER CHUNK (qids to run; already-done are skipped by STEP 0) =====
const QIDS = ["116", "405", "515", "540", "618", "673", "675", "685", "688", "692", "700", "703", "706", "707", "712", "713", "717", "719", "723", "724", "726", "728", "731", "732", "734", "737", "738", "745", "749", "751", "753", "757", "763", "764", "781", "785", "787", "788", "791", "793", "797", "798", "809", "814", "826", "827", "830", "832", "834", "835", "840", "844", "852", "856", "871", "876", "882", "883", "886", "894", "896", "899", "904", "907", "916", "919", "920", "921", "922", "927", "932", "936", "941", "946", "947", "948", "950", "959", "961", "966", "968", "969", "972", "979", "980", "983", "991", "992", "996", "1004", "1005", "1007", "1008", "1010", "1015", "1016", "1018", "1019", "1022", "1023", "1025", "1029", "1032", "1039", "1040", "1043", "1045", "1047", "1048", "1049", "1052", "1058", "1061", "1065", "1068", "1076", "1077", "1078", "1083", "1091", "1093", "1094", "1095", "1101", "1108", "1128", "1131", "1134", "1149", "1152", "1155", "1158", "1162", "1164", "1172", "1174", "1176", "1177", "1179", "1182", "1184", "1187", "1190", "1191", "1192", "1193", "1194", "1195", "1198", "1200", "1207", "1210", "1213", "1214", "1215", "1216", "1217", "1218", "1219", "1220", "1221", "1223", "1225", "1226", "1227", "1228", "1231", "1237", "1238", "1239", "1246", "1247", "1248", "1252", "1253", "1257", "1258", "1262", "1264", "1265"];
// =========================================================================

const ROOT = "/Users/lingweigu/Research/agent-plus-stage2/artifacts/bcp_stage1";
const CM = ROOT + "/q_all/tools/cm.py";
const INP = (q) => ROOT + "/stage2/inputs/" + q + ".json";
const OUT = (q) => ROOT + "/stage2/runs_v2/" + q + ".json";

const SCHEMA = { type:"object", additionalProperties:false,
  required:["qid","n_hops","all_supported","all_necessary","comprehensive","keep"],
  properties:{
    qid:{type:"string"}, n_hops:{type:"integer"},
    n_supported:{type:"integer"}, n_necessary:{type:"integer"},
    all_supported:{type:"boolean"}, all_necessary:{type:"boolean"},
    comprehensive:{type:"boolean"}, missing_fact:{type:"string"},
    keep:{type:"boolean"},
    hops:{type:"array", items:{type:"object", additionalProperties:false,
      required:["clue","supported","necessary"],
      properties:{ clue:{type:"string"}, supported:{type:"boolean"}, necessary:{type:"boolean"},
        doc_ids:{type:"array",items:{type:"string"}}, note:{type:"string"} }}} } };

function prompt(q){return [
"You SCRUTINIZE a BrowseComp-plus question for a STRICT corpus-grounded benchmark. qid="+q+". Tools: Read, Bash, Write.",
"A question QUALIFIES (keep=true) iff: every NECESSARY hop is corpus-supported on ClimbMix, AND the necessary hops are COMPREHENSIVE (they jointly & uniquely determine the answer using ONLY the corpus, i.e. no missing discriminating hop).",
"",
"STEP 0 - If "+OUT(q)+" already exists (Bash: test -f), READ it and return its exact JSON as StructuredOutput; do NOT redo the work.",
"",
"STEP 1 - Read "+INP(q)+" : question, answer, and `hops` (the full decomposition into atomic clues).",
"",
"STEP 2 - SUPPORT (grounding) for EACH hop on ClimbMix (the answer may NOT be assumed; ground the hop's own fact):",
"  python3 "+CM+" search \"<query naming the hop's entities + fact>\" [hits=30..200] [preview=600]",
"  python3 "+CM+" doc <docid>",
"  Issue several expanded queries (synonyms, aliases, paraphrases); escalate hits; read promising docs in full.",
"  supported = true ONLY IF a specific ClimbMix doc states/establishes the hop's fact (copy a verbatim snippet into note); NOT merely topical, NOT from your own outside knowledge.",
"",
"STEP 3 - NECESSITY for EACH hop: would the answer still be UNIQUELY determined by the OTHER hops if this hop were removed?",
"  necessary = true if removing the hop would let a different entity/value satisfy the remaining hops (it adds discriminating power).",
"  necessary = false (REDUNDANT) if the remaining hops already uniquely pin the answer without it.",
"",
"STEP 4 - COMPREHENSIVENESS / NO MISSING HOP (judge the SET of NECESSARY hops together, not one at a time):",
"  (a) COVERAGE: enumerate every distinguishing constraint stated in the QUESTION; verify each maps to at least one NECESSARY hop. Any question constraint with no corresponding hop => a hop is MISSING.",
"  (b) UNIQUENESS (adversarial): treating ONLY the necessary hops as the constraints, SEARCH ClimbMix for a DIFFERENT entity/value that ALSO satisfies ALL of them. Issue real queries; actively try to find a competitor to the gold answer.",
"  comprehensive = true ONLY IF every question constraint is covered by a necessary hop AND no alternative corpus entity satisfies all the necessary hops (the gold answer is uniquely pinned by the necessary hops alone).",
"  Otherwise comprehensive = false; set missing_fact to the uncovered question constraint, or to the competing entity you found.",
"",
"STEP 5 - Aggregate and WRITE "+OUT(q)+" :",
"  all_supported = every hop supported; all_necessary = every hop necessary;",
"  keep = (n_necessary >= 1) AND (every NECESSARY hop is supported) AND comprehensive.",
"  File JSON = {qid, n_hops, n_supported, n_necessary, all_supported, all_necessary, comprehensive, missing_fact, keep, hops:[{clue, supported, doc_ids, necessary, note}]}",
"Return StructuredOutput with the same fields.",
].join("\n");}

log("Stage-2 v2 scrutiny (grounding + necessity + comprehensiveness): "+QIDS.length+" questions.");
const rows = await pipeline(
  QIDS,
  (q) => agent(prompt(q), { label:"scrut2:"+q, phase:"Scrutinize+", schema:SCHEMA, agentType:"general-purpose" })
);
const c = rows.filter(Boolean);
const keep = c.filter(r=>r.keep).map(r=>r.qid).sort();
const comp = c.filter(r=>r.comprehensive).map(r=>r.qid).sort();
const allsup = c.filter(r=>r.all_supported).map(r=>r.qid).sort();
const droppedForComp = c.filter(r=>r.all_supported && !r.comprehensive).map(r=>r.qid).sort();
log("v2 chunk: judged="+c.length+" all_supported="+allsup.length+" comprehensive="+comp.length+" KEEP="+keep.length+" | supported-but-NOT-comprehensive="+droppedForComp.length);
return { judged:c.length, all_supported:allsup.length, comprehensive:comp.length, keep:keep.length,
         keep_qids:keep, dropped_for_comprehensiveness:droppedForComp,
         rows:c.map(r=>({qid:r.qid, all_supported:r.all_supported, all_necessary:r.all_necessary, comprehensive:r.comprehensive, keep:r.keep, n_hops:r.n_hops, n_necessary:r.n_necessary, missing_fact:r.missing_fact||""})) };
