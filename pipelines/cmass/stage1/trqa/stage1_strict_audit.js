export const meta = {
  name: 'cmass-stage1-trqa-strict-audit',
  description: 'CMASS Stage 1 for Total Recall QA, the strict audit of every SUPPORTED projection (the BCP-65 standard adapted to aggregation questions): every question constraint represented as hops (one membership hop per entity, one attribute hop per entity where the operator needs a value); every hop grounded by FULL corpus document text; the aggregate recomputed over verified values equals gold; an adversarial completeness check for missing members. One agent per question, batches of 6, writes stage1/<set>/strict/<qid>.json. Resumes from disk via trqa_state.py.',
  whenToUse: 'Audit TRQA questions whose next step (trqa_state.py) is strict, after build_strict_inputs.py.',
  phases: [
    { title: 'State', detail: 'trqa_state.py before and after every agent: what exists on disk' },
    { title: 'Strict', detail: 'full-doc grounding + arithmetic + completeness (STRICT_AUDIT.md)' },
  ],
}

// args: {run, tools, set, qids}   qids omitted: every question whose next step is strict.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'                                // the run directory (holds cmass.json), absolute
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'    // this pipeline's tools directory, absolute
const S1 = TOOLS.replace(/\/tools\/?$/, '/stage1/trqa')
const SET = A.set || 'trqa'
const BATCH = 6                     // the real run audited in batches of 6 questions
// The real run's agents were agentType 'general-purpose' with no model set (README.md records which model ran).
const AGENT_TYPE = 'general-purpose'

const STR = { type: 'string' }
const STRS = { type: 'array', items: STR }
const BOOL = { type: 'boolean' }
const STATE_FIELDS = { set: STR, qid: STR, next: STR, stage: STR, outcome: STR, detail: STR, out_file: STR,
  input_cmd: STR, extreme_file: STR, errors: STRS, warnings: STRS }
const STATE = { type: 'object', properties: STATE_FIELDS, required: Object.keys(STATE_FIELDS) }
const LIST = {
  type: 'object',
  properties: { questions: { type: 'array', items: { type: 'object', properties: { set: STR, qid: STR, next: STR }, required: ['set', 'qid', 'next'] } } },
  required: ['questions'],
}
const REPORT = {
  type: 'object',
  properties: {
    qid: STR, completed: BOOL,
    failure: { type: 'string', description: 'why the work could not be finished; empty when completed' },
    constraints_covered: BOOL, all_grounded: BOOL, aggregate_ok: BOOL, complete: BOOL, keep: BOOL, reason: STR,
  },
  required: ['qid', 'completed', 'failure', 'keep', 'reason'],
}

const MECHANICAL = (cmd, what) => `Run exactly this one shell command and return what it prints. Do not run anything else and do not edit any file.

  cd ${RUN} && ${cmd}

It prints one JSON object ${what}. Return it as your structured output, copying every field value exactly as printed: do not summarize, correct or fill in anything.`

const STATE_TOOL = `python3 ${S1}/trqa_state.py --run ${RUN}`

const stateOf = async (qid, tag) => {
  const s = await agent(MECHANICAL(`${STATE_TOOL} state ${SET} ${qid} --json`,
    `{"questions": [ ... ]} with exactly one question object; return that one object. If the command fails or prints no question, return set "${SET}", qid "${qid}", next "error", the error text in detail, and empty values ("", []) for every other field`),
  { label: `state:${qid}:${tag}`, phase: 'State', schema: STATE, effort: 'low' })
  return s || { set: SET, qid, next: 'error', detail: 'the state agent returned nothing', errors: [], warnings: [] }
}

const worklist = async () => {
  if (A.qids && A.qids.length) return A.qids.map(String)
  const r = await agent(MECHANICAL(`${STATE_TOOL} list ${SET} --next strict --json`,
    '{"questions": [...]}; return every question with its set, qid and next'),
  { label: `state:list:${SET}`, phase: 'State', schema: LIST, effort: 'low' })
  return ((r && r.questions) || []).map((x) => String(x.qid))
}

const COMMON = `Work in the run directory: cd ${RUN} before every command. Corpus tools: ${TOOLS}/cm.py (always --stage 1 --step strict --set ${SET} --qid <qid>; search with --center).
Run corpus commands one at a time (the corpus service is shared). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const prompt = (s, round) => `You AUDIT one TotalRecall-QA aggregation question already marked SUPPORTED, under a STRICT corpus-grounded standard. qid=${s.qid} (set ${SET}). Tools: Read, Bash.
Read ${S1}/STRICT_AUDIT.md in full and follow it exactly. The question passes ONLY IF every information-bearing constraint is represented as a hop, every entity membership AND attribute value is grounded by FULL corpus document text, the recomputed aggregate equals the gold answer, and the entity set is COMPLETE (adversarial check).

1. Your input: ${s.input_cmd}
2. Write exactly one file: ${s.out_file} (the fields STRICT_AUDIT.md lists; keep = constraints_covered AND all_grounded AND aggregate_ok AND complete).${round ? `
   An earlier attempt left that file invalid (${s.detail}): fix it, keeping what holds.` : ''}
3. Run ${STATE_TOOL} check ${SET} strict ${s.qid} and fix every ERROR until it reports 0 errors.
Touch no other file. Return the summary fields.
${COMMON}`

const runOne = async (qid) => {
  let s = await stateOf(qid, 'start')
  const before = s.next
  const reports = []
  for (let round = 0; round < 2 && s.next === 'strict'; round++) {
    const r = await agent(prompt(s, round), { label: `tq:${qid}`, phase: 'Strict', schema: REPORT, agentType: AGENT_TYPE })
    reports.push(r ? { completed: r.completed, failure: r.failure, keep: r.keep } : null)
    s = await stateOf(qid, `strict${round + 1}`)
  }
  // from disk: dropped questions end at stage strict; kept ones wait for build_strict_list.py (or a backfill)
  const keep = s.next === 'strict' || s.next === 'error' ? null : !(s.stage === 'strict' && s.outcome === 'dropped')
  return { qid, before, after: s.next, keep, detail: s.detail, reports }
}

const items = await worklist()
if (!items.length) {
  log('No questions to audit (pass {qids}, or run build_strict_inputs.py first, or every SUPPORTED question is audited).')
  return { judged: 0 }
}
log(`TRQA strict audit: ${items.length} question(s), batches of ${BATCH}; run ${RUN}, set ${SET}`)
const all = []
for (let i = 0; i < items.length; i += BATCH) {
  const b = items.slice(i, i + BATCH)
  const rows = (await parallel(b.map((q) => () => runOne(q).catch((e) => ({ qid: q, after: 'error', keep: null, detail: String(e) }))))).filter(Boolean)
  all.push(...rows)
  log(`batch ${Math.floor(i / BATCH) + 1}/${Math.ceil(items.length / BATCH)}: done=${all.length} KEEP=${all.filter((r) => r.keep === true).length}`)
}
const keep = all.filter((r) => r.keep === true).map((r) => r.qid).sort()
const stuck = all.filter((r) => r.keep === null)
log(`STRICT KEEP: ${keep.length}/${all.length}` + (stuck.length ? `; not finished (rerun them): ${stuck.map((r) => r.qid).join(', ')}` : ''))
log('Next: python3 build_strict_list.py (it stops and names any kept question that needs a hand-written backfill).')
return { judged: all.length, keep: keep.length, keep_stems: keep, rows: all }
