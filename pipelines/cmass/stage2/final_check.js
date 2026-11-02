export const meta = {
  name: 'cmass-stage2-final-check',
  description: 'CMASS Stage 2, C6: a fresh agent check of every question the reviewer accepted, for sets whose config has final_check true: rereads the cited documents, searches for omissions, checks any departure from the source answer, and writes final_check/<set>/<qid>[.r<N>].json. Sets with final_check false are exempt and never reach this step. Resumes from disk (tools/stage2_state.py).',
  whenToUse: 'Final-check accepted CMASS questions whose next step (stage2_state.py) is final_check.',
  phases: [
    { title: 'State', detail: 'stage2_state.py before and after every step: what exists on disk' },
    { title: 'Check', detail: 'one final checker per accepted question (stage2/FINAL_CHECK.md); no edits to other files' },
  ],
}

// args: {set: "<set>", qids: ["<qid>", ...]}   qids omitted: every question of the set whose next step is final_check
//    or [{set: "<set>", qid: "<qid>"}, ...], or {items: [{set, qid}, ...]} (several sets)
// {run, tools} in args override the two path constants.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'                                // the run directory (holds cmass.json), absolute
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'    // this pipeline's tools directory, absolute
const GUIDES = TOOLS.replace(/\/tools\/?$/, '/stage2')

const STR = { type: 'string' }
const STRS = { type: 'array', items: STR }
const BOOL = { type: 'boolean' }
const STATE_FIELDS = {
  set: STR, qid: STR, revision: { type: 'integer' }, attempt: { type: 'integer' }, next: STR, detail: STR, stage: STR, reason: STR,
  trigger: { type: 'object', properties: { stage: STR, ref: STR, reasons: STRS, evidence_only: BOOL }, required: ['stage', 'ref', 'reasons', 'evidence_only'] },
  family: STR, group: STR, permitted: STRS, answer_may_change: STR, answer_type: STR, question: STR, answer_text: STR,
  question_changed: BOOL, answer_changed: BOOL, final_check_enabled: BOOL, review_key: STR, decision: STR,
  audit_file: STR, packet_file: STR, revision_file: STR,
  hunts: { type: 'array', items: { type: 'object', properties: { kind: STR, path: STR, done: BOOL }, required: ['kind', 'path', 'done'] } },
  verify_file: STR, final_check_file: STR, reviews_file: STR, record_errors: STRS,
}
const STATE = { type: 'object', properties: STATE_FIELDS, required: Object.keys(STATE_FIELDS) }
const LIST = {
  type: 'object',
  properties: { questions: { type: 'array', items: { type: 'object', properties: { set: STR, qid: STR, next: STR }, required: ['set', 'qid', 'next'] } } },
  required: ['questions'],
}
const REPORT = {
  type: 'object',
  properties: {
    qid: STR, completed: BOOL, failure: { type: 'string', description: 'why the work could not be finished; empty when completed' },
    status: { type: 'string', enum: ['passed', 'failed', 'not_written'] }, summary: STR,
  },
  required: ['qid', 'completed', 'failure', 'status', 'summary'],
}

const MECHANICAL = (cmd, what) => `Run exactly this one shell command and return what it prints. Do not run anything else and do not edit any file.

  cd ${RUN} && ${cmd}

It prints one JSON object ${what}. Return it as your structured output, copying every field value exactly as printed: do not summarize, correct or fill in anything.`

const stateOf = async (it, tag) => {
  const s = await agent(MECHANICAL(`python3 ${TOOLS}/stage2_state.py --json ${it.set} ${it.qid}`,
    `{"questions": [ ... ]} with exactly one question object; return that one object. If the command fails or prints no question, return set "${it.set}", qid "${it.qid}", next "error", the error text in detail, and empty values (0, false, "", []) for every other field`),
  { label: `state:${it.qid}:${tag}`, phase: 'State', schema: STATE, effort: 'low' })
  return s || { set: it.set, qid: it.qid, next: 'error', detail: 'the state agent returned nothing', record_errors: [], hunts: [] }
}

const worklist = async (nexts) => {
  if (Array.isArray(A) || A.items) return (Array.isArray(A) ? A : A.items).map((x) => ({ set: x.set, qid: String(x.qid) }))
  if (!A.set) return []
  if (A.qids && A.qids.length) return A.qids.map((q) => ({ set: A.set, qid: String(q) }))
  const r = await agent(MECHANICAL(`python3 ${TOOLS}/stage2_state.py ${A.set} --next ${nexts.join(',')} --brief --json`,
    '{"questions": [...]}; return every question with its set, qid and next'),
  { label: `state:list:${A.set}`, phase: 'State', schema: LIST, effort: 'low' })
  return ((r && r.questions) || []).map((x) => ({ set: x.set, qid: String(x.qid) }))
}

const COMMON = `Work in the run directory: cd ${RUN} before every command. Tools are in ${TOOLS}; record formats in ${GUIDES}/SCHEMA.md.
Run corpus commands one at a time (the corpus service is shared). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false (status not_written if you wrote no file) with the reason in failure. Never claim success for work you did not complete: the workflow checks the files on disk afterwards.`

const checkPrompt = (s, fix) => `You are the final checker (C6) of question ${s.qid} of set ${s.set}, revision ${s.revision}, which the reviewer accepted (review key ${s.review_key}). Your job is to find every way it falls short. Do not edit any file other than your own.
Read ${GUIDES}/FINAL_CHECK.md in full, and ${GUIDES}/AUDIT.md for the tools, grounding rules and shared requirements.

The accepted version: question "${s.question}"; answer "${s.answer_text}" (answer type ${s.answer_type}).${s.answer_changed ? ' The answer differs from the source answer: check that a permitted revision with a written rationale changed it and that it meets the completeness standard.' : ''}${s.question_changed ? ' The question differs from the source question: check the edits against the set\'s limits.' : ''}
Set family ${s.family} (group ${s.group}): permitted revisions ${s.permitted.join(', ')}; answer may change: ${s.answer_may_change}.
Read the audit ${s.audit_file}, the packet (python3 ${TOOLS}/packets.py ${s.set} ${s.qid}, then ${s.packet_file}: it lists the revision history), the hunter and verifier files (${s.hunts.map((h) => h.path).join(', ')}, ${s.verify_file}), and the reviewer's notes under "${s.review_key}" in ${s.reviews_file} (notes may explain inferences; they never supply facts).

Reread every cited document in context, search for omissions yourself (up to 6 queries, --hits 50), and write ${s.final_check_file} (SCHEMA.md "Final check") with "revision": ${s.revision} and exactly this version's question and answer.${fix ? `
The file has errors: ${fix.join(' | ')}. Fix them.` : ''}
Then run python3 ${TOOLS}/check_records.py ${s.set} ${s.qid} --only final_check until it reports 0 errors.
${COMMON}`

const brief = (r) => (r ? { completed: r.completed, failure: r.failure, status: r.status, summary: r.summary } : null)
const fcErrors = (s) => (s.record_errors || []).filter((e) => e.includes(` final_check.r${s.revision}`))

const runOne = async (it) => {
  let s = await stateOf(it, 'start')
  if (s.next !== 'final_check') return { ...it, before: s.next, after: s.next, detail: s.detail, skipped: true }
  const s0 = s
  const reports = [brief(await agent(checkPrompt(s0), { label: `final-check:${it.qid}`, phase: 'Check', schema: REPORT }))]
  s = await stateOf(it, 'checked')
  if (fcErrors(s).length) {
    reports.push(brief(await agent(checkPrompt(s0, fcErrors(s)), { label: `final-check-fix:${it.qid}`, phase: 'Check', schema: REPORT })))
    s = await stateOf(it, 'check-fixed')
  }
  if (fcErrors(s).length) return { ...it, before: 'final_check', after: 'final_check_errors', detail: s.record_errors.join(' | '), reports }
  return { ...it, before: 'final_check', after: s.next, revision: s.revision, stage: s.stage, reason: s.reason, detail: s.detail, reports }
}

const items = await worklist(['final_check'])
if (!items.length) {
  log('No questions to final-check (pass {set, qids} or [{set, qid}], or no accepted question of the set waits for C6; sets with final_check false are exempt).')
  return { checked: 0 }
}
log(`Final check (C6): ${items.length} question(s); run ${RUN}`)
const rows = (await pipeline(items, (it) => runOne(it).catch((e) => ({ ...it, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after C6: ${JSON.stringify(count)} (ready = passed; revise = a repairable gap, run revision.js and review again; excluded = unrepairable or a second failure)`)
const skipped = rows.filter((r) => r.skipped)
if (skipped.length) log(`skipped (next step is not final_check): ${skipped.map((r) => `${r.set}/${r.qid} (${r.after})`).join(', ')}`)
return { count, rows }
