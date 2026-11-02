export const meta = {
  name: 'cmass-stage1-trqa-final-gates',
  description: 'CMASS Stage 1 for Total Recall QA, the four final gates over the strict-audited questions (the DSQA final filtering pipeline adapted to aggregation questions): extreme atomization + full-document re-grounding with fresh verbatim snippets; adversarial set completeness (TRQA needs TOTAL recall: the any-one-member waiver does NOT apply); cross-hop coherence with the aggregate recomputed over verified values; a human-annotator read that may fix hops. Filter-only except the annotator. One agent per question per gate, in order; a failing question skips the later gates. Resumes from disk via trqa_state.py.',
  whenToUse: 'Run the gates on TRQA questions whose next step (trqa_state.py) is extreme, completeness, coherence or annotator, after build_strict_list.py.',
  phases: [
    { title: 'State', detail: 'trqa_state.py before and after every agent: what exists on disk' },
    { title: 'Extreme', detail: 'atomize question -> map atoms to hops -> full-doc re-ground -> fresh verbatim snippets' },
    { title: 'Completeness', detail: 'adversarial search for qualifying entities missing from the set' },
    { title: 'Coherence', detail: 'unit/vintage/entity/scope consistency + aggregate recomputed == gold' },
    { title: 'Annotator', detail: 'read each hop as a human annotator; fix or drop' },
  ],
}

// args: {run, tools, set, qids}   qids omitted: every question whose next step is one of the four gates.
// The real run first ran a 10-question calibration batch, fixed the extreme gate's prompt (operator and set
// closure exempt, as FINAL_GATES.md now says), voided and reran the 5 calibration drops, then ran the other 77.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'                                // the run directory (holds cmass.json), absolute
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'    // this pipeline's tools directory, absolute
const S1 = TOOLS.replace(/\/tools\/?$/, '/stage1/trqa')
const SET = A.set || 'trqa'
const GATES = ['extreme', 'completeness', 'coherence', 'annotator']
const GATE = {
  extreme: { n: 1, title: 'Gate 1: extreme audit', phase: 'Extreme', label: 'x', tools: 'Read, Bash, Write',
    lead: 'You perform an EXTREME-STRICTNESS grounding audit of one TotalRecallQA question projected onto the corpus. You FILTER, never repair.' },
  completeness: { n: 2, title: 'Gate 2: adversarial completeness', phase: 'Completeness', label: 'cmp', tools: 'Read, Bash',
    lead: "You attack ONE TotalRecallQA question's entity set: your job is to FIND A MEMBER THEY MISSED (at least 8 and up to 20 distinct searches)." },
  coherence: { n: 3, title: 'Gate 3: coherence and recomputation', phase: 'Coherence', label: 'coh', tools: 'Read, Bash',
    lead: "You judge whether ONE TotalRecallQA question's hops COHERE into a justification of its numeric answer, and recompute it." },
  annotator: { n: 4, title: 'Gate 4: annotator pass', phase: 'Annotator', label: 'ann', tools: 'Read, Bash',
    lead: 'You are the human annotator for a corpus-grounded benchmark, reviewing ONE TotalRecallQA question end to end.' },
}
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
    keep: BOOL, reason: STR,
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
  const r = await agent(MECHANICAL(`${STATE_TOOL} list ${SET} --next ${GATES.join(',')} --json`,
    '{"questions": [...]}; return every question with its set, qid and next'),
  { label: `state:list:${SET}`, phase: 'State', schema: LIST, effort: 'low' })
  return ((r && r.questions) || []).map((x) => String(x.qid))
}

const COMMON = (g) => `Work in the run directory: cd ${RUN} before every command. Corpus tools: ${TOOLS}/cm.py (always --stage 1 --step ${g} --set ${SET} --qid <qid>; search with --center).
Run corpus commands one at a time (the corpus service is shared). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const prompt = (g, s, round) => {
  const G = GATE[g]
  return `${G.lead} qid=${s.qid} (set ${SET}). Tools: ${G.tools}.
Read ${S1}/FINAL_GATES.md: the sections "Tools", "The shape of these questions" and "${G.title}", and follow them exactly.

1. Your input: ${s.input_cmd}${g === 'extreme' ? '' : `
   The re-grounded hops of the extreme gate (use them, not the input's snippets): ${s.extreme_file}`}
2. Write exactly one file: ${s.out_file} (the fields "${G.title}" lists).${round ? `
   An earlier attempt left that file invalid (${s.detail}): fix it, keeping what holds.` : ''}
3. Run ${STATE_TOOL} check ${SET} ${g} ${s.qid} and fix every ERROR until it reports 0 errors.
Touch no other file. Return the summary fields.
${COMMON(g)}`
}

const runOne = async (qid) => {
  let s = await stateOf(qid, 'start')
  const before = s.next
  const reports = []
  for (let step = 0; step < 2 * GATES.length && GATES.includes(s.next); step++) {
    const g = s.next
    const round = reports.filter((r) => r.gate === g).length
    if (round >= 2) break                     // two attempts per gate; the file is still invalid: leave it for a rerun
    const r = await agent(prompt(g, s, round), { label: `${GATE[g].label}:${qid}`, phase: GATE[g].phase, schema: REPORT, agentType: AGENT_TYPE })
    reports.push({ gate: g, completed: r ? r.completed : false, failure: r ? r.failure : 'no report', keep: r ? r.keep : null })
    s = await stateOf(qid, `${g}${round + 1}`)
  }
  return { qid, before, after: s.next, stage: s.stage, outcome: s.outcome, detail: s.detail, reports }
}

const items = await worklist()
if (!items.length) {
  log('No questions for the gates (pass {qids}, or run build_strict_list.py first, or every question has finished the gates).')
  return { judged: 0 }
}
log(`TRQA final gates: ${items.length} question(s) through Extreme -> Completeness -> Coherence -> Annotator; run ${RUN}, set ${SET}`)
const rows = (await pipeline(items, (q) => runOne(q).catch((e) => ({ qid: q, after: 'error', detail: String(e) })))).filter(Boolean)
const dropped = {}
for (const g of GATES) dropped[g] = rows.filter((r) => r.after === 'done' && r.stage === g && r.outcome === 'dropped').map((r) => r.qid).sort()
const survivors = rows.filter((r) => r.after === 'done' && r.stage === 'annotator' && r.outcome === 'kept').map((r) => r.qid).sort()
const stuck = rows.filter((r) => r.after !== 'done')
log(`SURVIVORS: ${survivors.length}/${rows.length} | dropped ${GATES.map((g) => `${g}=${dropped[g].length}`).join(' ')}`)
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => `${r.qid} (${r.after})`).join(', ')}`)
log('Next: python3 build_final.py, then write_stage2_inputs.py.')
return { judged: rows.length, survivors: survivors.length, survivor_ids: survivors, dropped, rows }
