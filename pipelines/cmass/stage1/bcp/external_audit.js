export const meta = {
  name: 'cmass-stage1-bcp-external-audit',
  description: 'BrowseComp-Plus Stage 1, audit of independent projections: one agent per projection checks that its hops cover the question and chain to the answer, and fetches every cited corpus document to confirm it supports its hop (a document id alone is not enough). Writes stage1/<set>/external_audit/<qid>.json. Resumes from disk: bcp_state.py decides what is pending and validates every output.',
  whenToUse: 'After bcp_build.py external: audit the independent projections whose audit output is missing or invalid.',
  phases: [
    { title: 'State', detail: 'bcp_state.py before and after every agent: what exists on disk' },
    { title: 'Audit', detail: 'coverage/chain + retrieval-verified grounding (EXTERNAL_AUDIT.md)' },
  ],
}

// Ported from the private stage2/audit_sahel.js (PROJECTABLE questions our chain had not kept) and
// stage2/audit_sahel33.js (questions our answerability check labelled PARTIAL or NOT): the same check, one workflow.
// args: {run, tools, set, qids}: absolute paths; qids omitted = every candidate in external_candidates.jsonl still pending.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'
const STAGE1 = A.stage1 || TOOLS.replace(/\/tools\/?$/, '/stage1/bcp')
const SET = A.set || 'bcp'
const STEP = 'external_audit'
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
    keep: BOOL, summary: STR,
  },
  required: ['qid', 'completed', 'failure', 'keep', 'summary'],
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

const COMMON = `Work in the run directory: cd ${RUN} before every command. Stage 1 tools are in ${STAGE1}; the corpus CLI is ${TOOLS}/cm.py (always pass --stage 1 --step ${STEP} --set ${SET} --qid <qid> to doc, search and history: this step keeps its own search log; pass --center to search).
Run corpus commands one at a time (the corpus service is shared and rate-limited). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const prompt = (s, fix) => `You AUDIT another author's projection of one BrowseComp-Plus question for a strict corpus-grounded benchmark. qid=${s.qid} (set ${SET}).
Read ${STAGE1}/EXTERNAL_AUDIT.md in full and follow it exactly:
1. python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --input   then read the file it prints: question, answer and ${s.n_hops} hops with their cited corpus_doc_ids.
2. COVERAGE + CHAIN: all_hops_present (no necessary step missing) and chain_ok (the hops logically pin the answer).
3. GROUNDING (RETRIEVE, do not trust doc ids): fetch every cited document with ${TOOLS}/cm.py doc --stage 1 --step ${STEP} --set ${SET} --qid ${s.qid} DOCID --max 200000 and decide whether its CONTENT genuinely supports the hop (same entities and fact; not on-topic only, not a different person or place, not a keyword match). Record doc_used and a one-line why per hop.
4. keep = all_hops_present AND chain_ok AND every hop grounded. Write ${s.output}, one per_hop entry per input hop, in order.${fix ? `
   A previous attempt left that file missing or invalid (${s.detail}${s.errors && s.errors.length ? ': ' + s.errors.slice(0, 5).join('; ') : ''}): fix it, keeping what holds.` : ''}
5. Run python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --check and fix every ERROR until it prints OK. Touch no other file.
${COMMON}`

const runOne = async (qid) => {
  let s = await stateOf(qid, 'start')
  const before = s.status
  if (s.status === 'error' || s.status === 'not_a_target') return { qid, before, after: s.status, detail: s.detail }
  const reports = []
  for (let round = 0; round < 2 && (s.status === 'pending' || s.status === 'invalid'); round++) {
    const r = await agent(prompt(s, s.status === 'invalid'), { label: `saudit:${qid}`, phase: 'Audit', schema: REPORT, agentType: AGENT_TYPE })
    reports.push(r ? { completed: r.completed, failure: r.failure, keep: r.keep, summary: r.summary } : null)
    s = await stateOf(qid, `after${round + 1}`)
  }
  return { qid, before, after: s.status, errors: s.errors, reports }
}

const items = await worklist()
if (!items.length) {
  log(`No independent projections to audit (pass {set, qids}; build external_candidates.jsonl first).`)
  return { judged: 0 }
}
log(`Independent-projection audit (retrieval-verified): ${items.length} candidate(s); run ${RUN}, set ${SET}`)
const rows = (await pipeline(items, (q) => runOne(q).catch((e) => ({ qid: q, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after the audit: ${JSON.stringify(count)} (bcp_build.py external-final decides what is kept)`)
const stuck = rows.filter((r) => r.after !== 'done')
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => r.qid).join(', ')}`)
return { count, rows }
