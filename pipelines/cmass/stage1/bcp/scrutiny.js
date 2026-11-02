export const meta = {
  name: 'cmass-stage1-bcp-scrutiny',
  description: 'BrowseComp-Plus Stage 1, hop scrutiny: one fresh agent per PROJECTABLE question re-checks every hop of the full decomposition on the corpus. Pass 1: support and necessity. Pass 2: support, necessity and comprehensiveness. Writes stage1/<set>/scrutiny1|scrutiny2/<qid>.json. Resumes from disk: bcp_state.py decides what is pending and validates every output.',
  whenToUse: 'Run scrutiny pass 1 or pass 2 (args.pass) for PROJECTABLE questions whose output is missing or invalid; run both passes before bcp_build.py all-hops.',
  phases: [
    { title: 'State', detail: 'bcp_state.py before and after every agent: what exists on disk' },
    { title: 'Scrutinize', detail: 'pass 1: per-hop support (ClimbMix) + necessity (SCRUTINY.md)' },
    { title: 'Scrutinize+', detail: 'pass 2: grounding + necessity + comprehensiveness (SCRUTINY.md)' },
  ],
}

// Ported from the private stage2/scrutinize_hops.js (pass 1) and stage2/scrutinize_hops_v2.js (pass 2). The two
// passes are independent: run them in either order or at the same time; neither reads the other's output.
// args: {run, tools, set, pass: 1 | 2, qids}: absolute paths; qids omitted = every PROJECTABLE question pending for the pass.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'
const STAGE1 = A.stage1 || TOOLS.replace(/\/tools\/?$/, '/stage1/bcp')
const SET = A.set || 'bcp'
const PASS = String(A.pass || '1')
const STEP = PASS === '2' ? 'scrutiny2' : 'scrutiny1'
const PHASE = PASS === '2' ? 'Scrutinize+' : 'Scrutinize'
// The originals set no model: their agents (agent type general-purpose) inherited the session's model.
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
    n_supported: { type: 'integer' }, n_necessary: { type: 'integer' }, summary: STR,
  },
  required: ['qid', 'completed', 'failure', 'n_supported', 'n_necessary', 'summary'],
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

const ROLE = PASS === '2'
  ? 'You SCRUTINIZE a BrowseComp-Plus question for a STRICT corpus-grounded benchmark. It QUALIFIES iff every NECESSARY hop is corpus-supported on ClimbMix AND the necessary hops are COMPREHENSIVE (they jointly and uniquely determine the answer using ONLY the corpus).'
  : 'You SCRUTINIZE the hops of a BrowseComp-Plus question to decide if it qualifies for a strict benchmark: EVERY hop must be (a) supported on the ClimbMix corpus and (b) necessary.'

const prompt = (s, fix) => `${ROLE} qid=${s.qid} (set ${SET}, ${STEP}).
Read ${STAGE1}/SCRUTINY.md in full and follow the section "Pass ${PASS}" exactly; ignore the other pass.
1. python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --input   then read the file it prints: question, answer and the ${s.n_hops} hop clues.
2. Check every hop on ClimbMix (the answer may NOT be assumed; a specific document must state the hop's fact; copy a verbatim snippet into note)${PASS === '2' ? ', its necessity, and the comprehensiveness of the necessary hops (coverage of every question constraint, and an adversarial search for a competing answer)' : ' and its necessity'}.
3. Write ${s.output}: exactly ${s.n_hops} entries in hops, one per input hop, in the input order.${fix ? `
   A previous attempt left that file missing or invalid (${s.detail}${s.errors && s.errors.length ? ': ' + s.errors.slice(0, 5).join('; ') : ''}): fix it, keeping what holds.` : ''}
4. Run python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --check and fix every ERROR until it prints OK. Touch no other file.
${COMMON}`

const runOne = async (qid) => {
  let s = await stateOf(qid, 'start')
  const before = s.status
  if (s.status === 'error' || s.status === 'not_a_target') return { qid, before, after: s.status, detail: s.detail }
  const reports = []
  for (let round = 0; round < 2 && (s.status === 'pending' || s.status === 'invalid'); round++) {
    const r = await agent(prompt(s, s.status === 'invalid'), { label: `${STEP}:${qid}`, phase: PHASE, schema: REPORT, agentType: AGENT_TYPE })
    reports.push(r ? { completed: r.completed, failure: r.failure, summary: r.summary } : null)
    s = await stateOf(qid, `after${round + 1}`)
  }
  return { qid, before, after: s.status, errors: s.errors, reports }
}

const items = await worklist()
if (!items.length) {
  log(`No questions pending for ${STEP} (pass {set, pass, qids}, or every PROJECTABLE question of set ${SET} has a valid output; build scrutiny_inputs.jsonl first).`)
  return { judged: 0 }
}
log(`Hop scrutiny pass ${PASS}: ${items.length} question(s); run ${RUN}, set ${SET}`)
const rows = (await pipeline(items, (q) => runOne(q).catch((e) => ({ qid: q, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after ${STEP}: ${JSON.stringify(count)} (bcp_build.py all-hops decides what is grounded)`)
const stuck = rows.filter((r) => r.after !== 'done')
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => r.qid).join(', ')}`)
return { count, rows }
