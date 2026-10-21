export const meta = {
  name: 'cmass-stage1-bcp-expansion',
  description: 'BrowseComp-Plus Stage 1, expansion audit of the unused PROJECTABLE questions: one agent per question applies the strict verbatim standard with a rescue search and a coverage check; for a question that does not qualify, a second agent re-judges its failing hops at the standard of the human reviewers (core entity and relation grounded, secondary qualifier waived). Writes stage1/<set>/strict_audit/<qid>.json and stage1/<set>/rejudge/<qid>.json. Resumes from disk.',
  whenToUse: 'After bcp_build.py expansion-pool: find the near misses among the PROJECTABLE questions that did not become candidates.',
  phases: [
    { title: 'State', detail: 'bcp_state.py before and after every agent: what exists on disk' },
    { title: 'Strict', detail: 'verbatim full-document grounding + rescue search + coverage (EXPANSION.md)' },
    { title: 'Rejudge', detail: 'failing hops at the human reviewers standard; coverage gaps stay failures (EXPANSION.md)' },
  ],
}

// The prompts of these two steps were not committed in the private repository; EXPANSION.md states the standard
// recorded in stage3/EXPANSION_326.md and stage3/VERIFIED53.md. Neither a model nor an agent type was recorded (the
// commit is co-authored by Claude, without a version), so both are left unset here.
// args: {run, tools, set, qids}: absolute paths; qids omitted = every question of expansion_pool.jsonl still pending.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'
const STAGE1 = A.stage1 || TOOLS.replace(/\/tools\/?$/, '/stage1/bcp')
const SET = A.set || 'bcp'

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
    result: { type: 'string', description: 'strict: qualifies or not; rejudge: survive, fail_core or fail_coverage' },
    summary: STR,
  },
  required: ['qid', 'completed', 'failure', 'result', 'summary'],
}

const MECHANICAL = (cmd, what) => `Run exactly this one shell command and return what it prints. Do not run anything else and do not edit any file.

  cd ${RUN} && ${cmd}

It prints one JSON object ${what}. Return it as your structured output, copying every field value exactly as printed: do not summarize, correct or fill in anything.`

const stateOf = async (step, qid, tag) => {
  const s = await agent(MECHANICAL(`python3 ${STAGE1}/bcp_state.py ${SET} ${step} ${qid} --json`,
    `{"questions": [ ... ]} with exactly one question object; return that one object. If the command fails or prints no question, return set "${SET}", step "${step}", qid "${qid}", status "error", the error text in detail, and empty values ("", 0, []) for every other field`),
  { label: `state:${step}:${qid}:${tag}`, phase: 'State', schema: STATE, effort: 'low' })
  return s || { set: SET, step, qid, status: 'error', errors: [], detail: 'the state agent returned nothing' }
}

const worklist = async () => {
  if (A.qids && A.qids.length) return A.qids.map(String)
  const out = new Set()
  for (const step of ['strict_audit', 'rejudge']) {
    const r = await agent(MECHANICAL(`python3 ${STAGE1}/bcp_state.py ${SET} ${step} --pending --brief --json`,
      '{"questions": [...]}; return every question with its set, qid, next and status'),
    { label: `state:list:${step}`, phase: 'State', schema: LIST, effort: 'low' })
    for (const x of (r && r.questions) || []) out.add(String(x.qid))
  }
  return [...out]
}

const COMMON = (step) => `Work in the run directory: cd ${RUN} before every command. Stage 1 tools are in ${STAGE1}; the corpus CLI is ${TOOLS}/cm.py (always pass --stage 1 --step ${step} --set ${SET} --qid <qid> to search, doc and history: this step keeps its own search log; pass --center to search: previews centred on the query match).
Run corpus commands one at a time (the corpus service is shared and rate-limited). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const fixNote = (s) => (s.status === 'invalid'
  ? `\n   A previous attempt left that file invalid (${s.detail}${s.errors && s.errors.length ? ': ' + s.errors.slice(0, 5).join('; ') : ''}): fix it, keeping what holds.`
  : '')

const strictPrompt = (s) => `You audit one BrowseComp-Plus question at the human reviewers' strict standard. qid=${s.qid} (set ${SET}).
Read ${STAGE1}/EXPANSION.md in full and follow the section "Step strict_audit" exactly:
1. python3 ${STAGE1}/bcp_state.py ${SET} strict_audit ${s.qid} --input   then read the file it prints (${s.n_hops} hops with the documents earlier passes found).
2. Read every cited document IN FULL; a hop is SUPPORTED only when ClimbMix text states its fact verbatim (right entity, explicit relation, in-text dates and numbers, no inference-only support; union across documents allowed). Attempt a genuine rescue by deep search for every hop that is not SUPPORTED. Check coverage of every clue of the question.
3. Write ${s.output}, one hops entry per input hop, in order.${fixNote(s)}
4. Run python3 ${STAGE1}/bcp_state.py ${SET} strict_audit ${s.qid} --check and fix every ERROR until it prints OK. Touch no other file.
${COMMON('strict_audit')}`

const rejudgePrompt = (s) => `You re-judge the failing hops of one BrowseComp-Plus question at the standard the human reviewers apply. qid=${s.qid} (set ${SET}).
Read ${STAGE1}/EXPANSION.md in full and follow the section "Step rejudge" exactly:
1. python3 ${STAGE1}/bcp_state.py ${SET} rejudge ${s.qid} --input   then read the file it prints (the hops and the strict audit's result).
2. A hop passes when its CORE entity and relation are grounded even if a secondary qualifier is missing (name the waived qualifier); it fails when the evidence concerns a different entity or the core fact is absent. Coverage gaps stay failures.
3. Write ${s.output} with every hop of the question, in order, and the outcome survive / fail_core / fail_coverage.${fixNote(s)}
4. Run python3 ${STAGE1}/bcp_state.py ${SET} rejudge ${s.qid} --check and fix every ERROR until it prints OK. Touch no other file.
${COMMON('rejudge')}`

const runStep = async (step, qid, phase, mk, label) => {
  let s = await stateOf(step, qid, 'start')
  const reports = []
  for (let round = 0; round < 2 && (s.status === 'pending' || s.status === 'invalid'); round++) {
    const r = await agent(mk(s), { label: `${label}:${qid}`, phase, schema: REPORT })
    reports.push(r ? { completed: r.completed, failure: r.failure, result: r.result } : null)
    s = await stateOf(step, qid, `after${round + 1}`)
  }
  return { status: s.status, errors: s.errors, reports }
}

const runOne = async (qid) => {
  const strict = await runStep('strict_audit', qid, 'Strict', strictPrompt, 'strict')
  if (strict.status !== 'done') return { qid, strict: strict.status, rejudge: '', errors: strict.errors }
  const rj = await stateOf('rejudge', qid, 'check')
  if (rj.status === 'not_a_target') return { qid, strict: 'qualifies', rejudge: '' }
  const r2 = await runStep('rejudge', qid, 'Rejudge', rejudgePrompt, 'rejudge')
  return { qid, strict: 'done', rejudge: r2.status, errors: r2.errors }
}

const items = await worklist()
if (!items.length) {
  log(`No expansion audits pending (pass {set, qids}; build expansion_pool.jsonl first).`)
  return { judged: 0 }
}
log(`Expansion audit: ${items.length} question(s); run ${RUN}, set ${SET}`)
const rows = (await pipeline(items, (q) => runOne(q).catch((e) => ({ qid: q, strict: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) {
  const k = `strict:${r.strict}${r.rejudge ? ' rejudge:' + r.rejudge : ''}`
  count[k] = (count[k] || 0) + 1
}
log(`after the expansion audit: ${JSON.stringify(count)} (bcp_build.py expansion decides the survivors)`)
const stuck = rows.filter((r) => !(r.strict === 'qualifies' || r.rejudge === 'done'))
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => r.qid).join(', ')}`)
return { count, rows }
