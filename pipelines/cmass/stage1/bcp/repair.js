export const meta = {
  name: 'cmass-stage1-bcp-repair',
  description: 'BrowseComp-Plus Stage 1, near-miss repair: one agent per near miss (a PROJECTABLE question with all hops grounded except one or two) searches the corpus iteratively for the failing hops only and records a fixing document, read in full, or why a hop cannot be fixed. Writes stage1/<set>/repair/<qid>.json; batches of at most 12 agents. Resumes from disk: bcp_state.py decides what is pending and validates every output.',
  whenToUse: 'After bcp_build.py repair-pool: repair phase A (args.phase "A") or phase B ("B") near misses whose repair output is missing or invalid.',
  phases: [
    { title: 'State', detail: 'bcp_state.py before and after every agent: what exists on disk' },
    { title: 'Repair', detail: 'iterative search + content-verified grounding for failing hops (REPAIR.md)' },
  ],
}

// Ported from the private stage2/repair_nearmiss.js (phase A ran first on 65 targets, phase B later on 84, in batches
// of at most 12 concurrent agents). A claimed repair is only a claim: the operator verifies it (MANUAL.md, "repairs").
// args: {run, tools, set, phase: "A" | "B", qids}: absolute paths; qids omitted = every pending target of the phase.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'
const STAGE1 = A.stage1 || TOOLS.replace(/\/tools\/?$/, '/stage1/bcp')
const SET = A.set || 'bcp'
const PHASE_ARG = A.phase === 'B' ? 'B' : 'A'
const STEP = 'repair'
const BATCH = 12          // the original's maximum number of concurrent repair agents
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
    repaired_all: BOOL, summary: STR,
  },
  required: ['qid', 'completed', 'failure', 'repaired_all', 'summary'],
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
  const r = await agent(MECHANICAL(`python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} --pending --phase ${PHASE_ARG} --brief --json`,
    '{"questions": [...]}; return every question with its set, qid, next and status'),
  { label: `state:list:${STEP}:${PHASE_ARG}`, phase: 'State', schema: LIST, effort: 'low' })
  return ((r && r.questions) || []).map((x) => String(x.qid))
}

const COMMON = `Work in the run directory: cd ${RUN} before every command. Stage 1 tools are in ${STAGE1}; the corpus CLI is ${TOOLS}/cm.py (always pass --stage 1 --step ${STEP} --set ${SET} --qid <qid> to search, doc and history: this step keeps its own search log; pass --center to search: previews centred on the query match).
Run corpus commands one at a time (the corpus service is shared and rate-limited). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const prompt = (s, fix) => `You REPAIR one near-miss BrowseComp-Plus question for a strict corpus-grounded benchmark. qid=${s.qid} (set ${SET}, phase ${PHASE_ARG}).
Read ${STAGE1}/REPAIR.md in full and follow it exactly:
1. python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --input   then read the file it prints. Hops with FAILING=true lack ClimbMix grounding; all others are already grounded. Your job is ONLY the FAILING hops.
2. For EACH FAILING hop try 4-8 varied queries (synonyms, aliases, alternate phrasings, the bridge or component fact, broader then narrower; escalate hits to 100-200) and read promising documents IN FULL. A hop is FIXED only if a document's retrieved CONTENT genuinely states the hop's fact about the SAME entity (not a keyword collision, not merely on-topic, not your own knowledge); copy a VERBATIM snippet. Use prior_note to avoid dead ends, but re-check honestly.
3. If a hop cannot be fixed after honest effort, record it as unfixable with a one-line why and stop spending on it.
4. Write ${s.output} = {qid, repaired_all, fixed: [{index, clue, doc_id, snippet}], unfixable: [{index, clue, why}]}, naming every FAILING hop by index.${fix ? `
   A previous attempt left that file missing or invalid (${s.detail}${s.errors && s.errors.length ? ': ' + s.errors.slice(0, 5).join('; ') : ''}): fix it, keeping what holds.` : ''}
5. Run python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --check and fix every ERROR until it prints OK. Touch no other file.
${COMMON}`

const runOne = async (qid) => {
  let s = await stateOf(qid, 'start')
  const before = s.status
  if (s.status === 'error' || s.status === 'not_a_target') return { qid, before, after: s.status, detail: s.detail }
  const reports = []
  for (let round = 0; round < 2 && (s.status === 'pending' || s.status === 'invalid'); round++) {
    const r = await agent(prompt(s, s.status === 'invalid'), { label: `fix:${qid}`, phase: 'Repair', schema: REPORT, agentType: AGENT_TYPE })
    reports.push(r ? { completed: r.completed, failure: r.failure, repaired_all: r.repaired_all, summary: r.summary } : null)
    s = await stateOf(qid, `after${round + 1}`)
  }
  return { qid, before, after: s.status, errors: s.errors, reports }
}

const items = await worklist()
if (!items.length) {
  log(`No phase ${PHASE_ARG} repairs pending (pass {set, phase, qids}; build repair_pool.jsonl first).`)
  return { judged: 0 }
}
log(`Near-miss repair, phase ${PHASE_ARG}: ${items.length} question(s), batches of ${BATCH}; run ${RUN}, set ${SET}`)
const rows = []
for (let i = 0; i < items.length; i += BATCH) {
  const batch = items.slice(i, i + BATCH)
  const got = await parallel(batch.map((q) => () => runOne(q).catch((e) => ({ qid: q, after: 'error', detail: String(e) }))))
  rows.push(...got.filter(Boolean))
  const claimed = rows.filter((r) => r.reports && r.reports.some((x) => x && x.repaired_all)).length
  log(`batch ${Math.floor(i / BATCH) + 1}/${Math.ceil(items.length / BATCH)} done; repairs claimed so far ${claimed}/${rows.length}`)
}
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after repair: ${JSON.stringify(count)}. Next: bcp_build.py repaired, then verify every claim by hand (MANUAL.md, repairs)`)
const stuck = rows.filter((r) => r.after !== 'done')
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => r.qid).join(', ')}`)
return { count, rows }
