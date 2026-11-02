export const meta = {
  name: 'cmass-stage1-bcp-project',
  description: 'BrowseComp-Plus Stage 1, projection: one agent per task decomposes the question into hops, grounds them on the corpus (inference allowed) and labels it PROJECTABLE, PARTIAL or NOT, writing stage1/<set>/project/<qid>.json. Resumes from disk: bcp_state.py decides what is pending and validates every output.',
  whenToUse: 'Project BrowseComp-Plus tasks (tasks/<set>.jsonl) whose projection output is missing or invalid.',
  phases: [
    { title: 'State', detail: 'bcp_state.py before and after every agent: what exists on disk' },
    { title: 'Project', detail: 'one agent per question (PROJECT.md); bcp_state.py --check until OK' },
  ],
}

// Ported from the private q_all/tools/project_chunk.js (the original ran chunks of 30-100 qids set in a QIDS constant).
// args: {run, tools, set, qids}: absolute paths; qids omitted = every task whose projection is missing or invalid.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'                                // the run directory (holds cmass.json), absolute
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'    // pipelines/cmass/tools, absolute
const STAGE1 = A.stage1 || TOOLS.replace(/\/tools\/?$/, '/stage1/bcp')
const SET = A.set || 'bcp'
const STEP = 'project'
// The original set no model: its agents (agent type general-purpose) inherited the session's model.
const AGENT_TYPE = 'general-purpose'

const STR = { type: 'string' }
const BOOL = { type: 'boolean' }
const STATE_FIELDS = {
  set: STR, step: STR, qid: STR, status: STR, output: STR, input: STR, guide: STR,
  n_hops: { type: 'integer' }, errors: { type: 'array', items: STR }, detail: STR,
}
const STATE = { type: 'object', properties: STATE_FIELDS, required: Object.keys(STATE_FIELDS) }
const LIST = {
  type: 'object',
  properties: { questions: { type: 'array', items: { type: 'object', properties: { set: STR, qid: STR, next: STR, status: STR }, required: ['set', 'qid', 'next', 'status'] } } },
  required: ['questions'],
}
const REPORT = {
  type: 'object',
  properties: {
    qid: STR, completed: BOOL,
    failure: { type: 'string', description: 'why the work could not be finished; empty when completed' },
    verdict: { type: 'string', description: 'PROJECTABLE, PARTIAL or NOT as written to the file; empty if no file' },
    summary: STR,
  },
  required: ['qid', 'completed', 'failure', 'verdict', 'summary'],
}

const MECHANICAL = (cmd, what) => `Run exactly this one shell command and return what it prints. Do not run anything else and do not edit any file.

  cd ${RUN} && ${cmd}

It prints one JSON object ${what}. Return it as your structured output, copying every field value exactly as printed: do not summarize, correct or fill in anything.`

const stateOf = async (qid, tag) => {
  const s = await agent(MECHANICAL(`python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${qid} --json`,
    `{"questions": [ ... ]} with exactly one question object; return that one object. If the command fails or prints no question, return set "${SET}", step "${STEP}", qid "${qid}", status "error", the error text in detail, and empty values ("", 0, []) for every other field`),
  { label: `state:${qid}:${tag}`, phase: 'State', schema: STATE, effort: 'low' })
  return s || { set: SET, step: STEP, qid, status: 'error', errors: [], detail: 'the state agent returned nothing' }
}

const worklist = async () => {
  if (A.qids && A.qids.length) return A.qids.map(String)
  const r = await agent(MECHANICAL(`python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} --pending --brief --json`,
    '{"questions": [...]}; return every question with its set, qid, next and status'),
  { label: `state:list:${STEP}`, phase: 'State', schema: LIST, effort: 'low' })
  return ((r && r.questions) || []).map((x) => String(x.qid))
}

const COMMON = `Work in the run directory: cd ${RUN} before every command. Stage 1 tools are in ${STAGE1}; the corpus CLI is ${TOOLS}/cm.py (always pass --stage 1 --step ${STEP} --set ${SET} --qid <qid> to search, doc and history: this step keeps its own search log; pass --center to search: previews centred on the query match).
Run corpus commands one at a time (the corpus service is shared and rate-limited). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const prompt = (s, fix) => `You are an inference-allowed PROJECTOR for BrowseComp-Plus -> ClimbMix. qid = ${s.qid} (set ${SET}).
GOAL: decide whether THIS question's answer can be UNIQUELY reasoned out from the ClimbMix corpus, and record the grounded reasoning.
Read ${STAGE1}/PROJECT.md in full and follow it exactly:
1. python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --input   then read the file it prints (question, answer, hints = the source benchmark's own documents, hints only).
2. Decompose into the minimal hops (aim 4-6); 3. ground each hop on ClimbMix (inference allowed; at least 3-6 varied queries per hop with --hits 30 --center; escalate hits 30 -> 100 -> 200); 4. decide PROJECTABLE / PARTIAL / NOT by the uniqueness test.
5. Write ${s.output} in the format of PROJECT.md, step 5.${fix ? `
   A previous attempt left that file missing or invalid (${s.detail}${s.errors && s.errors.length ? ': ' + s.errors.slice(0, 5).join('; ') : ''}): fix it, keeping what holds.` : ''}
6. Run python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --check and fix every ERROR until it prints OK. Touch no other file.
${COMMON}`

const runOne = async (qid) => {
  let s = await stateOf(qid, 'start')
  const before = s.status
  if (s.status === 'error' || s.status === 'not_a_target') return { qid, before, after: s.status, detail: s.detail }
  const reports = []
  for (let round = 0; round < 2 && (s.status === 'pending' || s.status === 'invalid'); round++) {
    const r = await agent(prompt(s, s.status === 'invalid'), { label: `project:${qid}`, phase: 'Project', schema: REPORT, agentType: AGENT_TYPE })
    reports.push(r ? { completed: r.completed, failure: r.failure, verdict: r.verdict, summary: r.summary } : null)
    s = await stateOf(qid, `after${round + 1}`)
  }
  const last = reports.filter(Boolean).slice(-1)[0]
  return { qid, before, after: s.status, verdict: s.status === 'done' && last ? last.verdict : '', errors: s.errors, reports }
}

const items = await worklist()
if (!items.length) {
  log(`No questions to project (pass {set, qids}, or every task of set ${SET} has a valid projection or a manual skip).`)
  return { projected: 0 }
}
log(`Projection: ${items.length} question(s); run ${RUN}, set ${SET}`)
const rows = (await pipeline(items, (q) => runOne(q).catch((e) => ({ qid: q, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
const tally = {}
for (const r of rows) {
  count[r.after] = (count[r.after] || 0) + 1
  if (r.verdict) tally[r.verdict] = (tally[r.verdict] || 0) + 1
}
log(`after projection: ${JSON.stringify(count)}; verdicts as reported (bcp_build.py verdicts reads the files): ${JSON.stringify(tally)}`)
const stuck = rows.filter((r) => r.after !== 'done')
if (stuck.length) log(`not finished (rerun them, or record a refusal in manual/skips.json): ${stuck.map((r) => r.qid).join(', ')}`)
return { count, tally, rows }
