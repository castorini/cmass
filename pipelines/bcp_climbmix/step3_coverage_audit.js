export const meta = {
  name: 'coverage-audit',
  description: 'Coverage-only audit (sharpened): flag a question if any substantive CONTENT phrase has no hop, with special attention to SPECIFIC NUMBERS/COUNTS/INTERVALS embedded in a phrase (a count, an N-day/N-year interval, an attendance, exact times) - a hop covering the surrounding event but omitting the number does NOT cover that number. Ignore "as of <year>" timestamp qualifiers. Grounding not checked. Data read from inputs/<qid>.json.',
  phases: [ { title: 'Coverage2', detail: 'every content sub-clue incl. specific numbers has a hop' } ],
}
// Reads each question's {question, answer, hops} from INPUTS/<qid>.json (produced by
// step3_build_inputs.py). The released script embeds no question text.
const INPUTS = "inputs";  // per-question {question, answer, hops} files from step3_build_inputs.py
const QIDS = ["25", "74", "156", "190", "205", "223", "234", "237", "275", "282", "333", "342", "362", "409", "436", "445", "500", "512", "569", "576", "610", "620", "638", "665", "688", "692", "724", "732", "749", "781", "788", "793", "798", "809", "830", "835", "840", "856", "899", "936", "947", "948", "968", "969", "1004", "1010", "1019", "1083", "1131", "1174", "1187", "1190", "1215", "1216", "1217", "1218", "1223", "1227", "1238", "1239", "1248", "1250", "1252"];
const SCHEMA = { type:"object", additionalProperties:false, required:["qid","coverage_ok","keep"],
  properties:{ qid:{type:"string"}, coverage_ok:{type:"boolean"}, uncovered_phrases:{type:"array",items:{type:"string"}},
    keep:{type:"boolean"}, reason:{type:"string"} } };
function prompt(q){ return [
"You check ONE thing: does every information-bearing CONTENT phrase in the QUESTION have a corresponding hop? qid="+q+". Tools: Read only. Do NOT assess how well hops are grounded - only whether each content phrase is REPRESENTED by a hop.",
"",
"DATA: Read "+INPUTS+"/"+q+".json - {question, answer, hops:[{clue,...}]}.",
"",
"A CONTENT phrase is a substantive constraint the answer depends on: a named entity, a relation between entities, a specific event, a superlative, or a quantity.",
"CRITICAL - SPECIFIC NUMBERS: when the question states a specific number, count, or interval - e.g. 'married three times', 'one daughter and one son', 'between 24 and 25 years later', 'renamed 539 days later', 'an attendance of 61,700', exact goal minutes - that NUMBER is itself a content sub-clue. A hop that mentions the surrounding event/entity but OMITS the specific number does NOT cover it; list the number as uncovered. Read each hop's clue text and check the actual number appears.",
"",
"List every uncovered CONTENT phrase (including omitted specific numbers) in uncovered_phrases.",
"IGNORE 'as of <year>' / 'in 2023' TIMESTAMP qualifiers - those are non-blocking, not sub-clues.",
"",
"coverage_ok = uncovered_phrases empty. keep = coverage_ok. One-line reason. Return StructuredOutput.",
].join("\n"); }
log("Coverage-only audit v2 (numbers): "+QIDS.length+" questions.");
const rows=(await pipeline(QIDS,(q)=>agent(prompt(q),{label:"cov2:"+q,phase:"Coverage2",schema:SCHEMA,agentType:"general-purpose"}))).filter(Boolean);
const keep=rows.filter(r=>r.coverage_ok).map(r=>r.qid).sort();
const drop=rows.filter(r=>!r.coverage_ok).map(r=>r.qid).sort();
log("coverage2: judged="+rows.length+" keep="+keep.length+" drop="+drop.length);
return { judged:rows.length, keep:keep.length, keep_qids:keep, dropped:drop,
  rows:rows.map(r=>({qid:r.qid, coverage_ok:r.coverage_ok, uncovered:r.uncovered_phrases||[], reason:r.reason||""})) };
