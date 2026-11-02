export const meta = {
  name: 'cmass-stage1-trqa-project',
  description: 'CMASS Stage 1 for Total Recall QA, the projection: one agent per question parses the entity-set constraint, operator and target attribute, recovers the COMPLETE entity set and each attribute from the corpus by BM25 (total recall), computes the aggregate and compares it to the gold value, and writes stage1/<set>/project/<qid>.json with verdict SUPPORTED / PARTIAL / NOT. Resumes from disk: trqa_state.py decides what is pending and validates every file.',
  whenToUse: 'Project TRQA tasks (tasks/<set>.jsonl) whose next step (trqa_state.py) is project.',
  phases: [
    { title: 'State', detail: 'trqa_state.py before and after every agent: what exists on disk' },
    { title: 'Project', detail: '1 agent/question: total-recall enumeration + attribute grounding + aggregate check (PROJECT.md)' },
  ],
}

// args: {run, tools, set, qids}   run/tools: absolute paths; set default "trqa"; qids omitted: every task whose next step is project.
// The real run passed chunks of about 200 question ids per launch (16 chunks over 2,327 questions).
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'                                // the run directory (holds cmass.json), absolute
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'    // this pipeline's tools directory, absolute
const S1 = TOOLS.replace(/\/tools\/?$/, '/stage1/trqa')
const SET = A.set || 'trqa'
// The real run's agents were agentType 'general-purpose' with no model set (README.md records which model ran).
const AGENT_TYPE = 'general-purpose'

const STR = { type: 'string' }
const STRS = { type: 'array', items: STR }
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
    qid: STR, completed: { type: 'boolean' },
    failure: { type: 'string', description: 'why the work could not be finished; empty when completed' },
    verdict: { type: 'string', enum: ['SUPPORTED', 'PARTIAL', 'NOT', ''] },
    n_entities_expected: { type: 'integer' }, n_entities_recovered: { type: 'integer' },
    matches_gold: { type: 'boolean' }, recall_complete: { type: 'boolean' },
  },
  required: ['qid', 'completed', 'failure', 'verdict', 'matches_gold', 'recall_complete'],
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
  const r = await agent(MECHANICAL(`${STATE_TOOL} list ${SET} --next project --json`,
    '{"questions": [...]}; return every question with its set, qid and next'),
  { label: `state:list:${SET}`, phase: 'State', schema: LIST, effort: 'low' })
  return ((r && r.questions) || []).map((x) => String(x.qid))
}

const COMMON = `Work in the run directory: cd ${RUN} before every command. Corpus tools: ${TOOLS}/cm.py (always --stage 1 --step project --set ${SET} --qid <qid>; search with --center).
Run corpus commands one at a time (the corpus service is shared). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const prompt = (s, round) => `You assess whether a TotalRecall-QA (TRQA) question can be ANSWERED FROM THE CORPUS. qid=${s.qid} (set ${SET}). Tools: Read, Bash, Write.
Read ${S1}/PROJECT.md in full and follow it exactly: parse the entity set, operator and target attribute; enumerate the COMPLETE set and ground each member's attribute by BM25 (total recall: many expanded queries, escalating 30 -> 100 -> 200 hits); compute the aggregate; compare it to the gold value; give the verdict SUPPORTED / PARTIAL / NOT.

1. Your task: ${s.input_cmd}
2. Write exactly one file: ${s.out_file} (the fields PROJECT.md lists).${round ? `
   An earlier attempt left that file invalid (${s.detail}): fix it, keeping what holds.` : ''}
3. Run ${STATE_TOOL} check ${SET} project ${s.qid} and fix every ERROR until it reports 0 errors.
Touch no other file. Return the summary fields.
${COMMON}`

const runOne = async (qid) => {
  let s = await stateOf(qid, 'start')
  const before = s.next
  const reports = []
  for (let round = 0; round < 2 && s.next === 'project'; round++) {
    const r = await agent(prompt(s, round), { label: `trqa:${qid}`, phase: 'Project', schema: REPORT, agentType: AGENT_TYPE })
    reports.push(r ? { completed: r.completed, failure: r.failure, verdict: r.verdict } : null)
    s = await stateOf(qid, `project${round + 1}`)
  }
  // verdict from disk: a non-SUPPORTED projection ends the question at stage project; SUPPORTED moves on
  const verdict = s.next === 'project' || s.next === 'error' ? 'pending' : (s.stage === 'project' ? s.outcome : 'SUPPORTED')
  return { qid, before, after: s.next, verdict, detail: s.detail, reports }
}

const items = await worklist()
if (!items.length) {
  log('No questions to project (pass {qids}, or every task already has a valid projection).')
  return { judged: 0 }
}
log(`TRQA projection: ${items.length} question(s) (total-recall enumeration + aggregate check); run ${RUN}, set ${SET}`)
const rows = (await pipeline(items, (q) => runOne(q).catch((e) => ({ qid: q, after: 'error', verdict: 'pending', detail: String(e) })))).filter(Boolean)
const tally = (v) => rows.filter((r) => r.verdict === v).length
log(`done: SUPPORTED=${tally('SUPPORTED')} PARTIAL=${tally('PARTIAL')} NOT=${tally('NOT')} pending=${tally('pending')}`)
const stuck = rows.filter((r) => r.verdict === 'pending')
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => r.qid).join(', ')}`)
return { judged: rows.length, SUPPORTED: tally('SUPPORTED'), PARTIAL: tally('PARTIAL'), NOT: tally('NOT'), rows }
