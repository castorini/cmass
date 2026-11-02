export const meta = {
  name: 'cmass-stage2-audit',
  description: 'CMASS Stage 2, C2-C3: one auditor agent per question writes work/<set>/<qid>.audit.json until validate.py reports 0 errors, then an independent second reader confirms or revises it. Resumes from disk: every step is decided by tools/stage2_state.py, never by an agent report.',
  whenToUse: 'Audit or re-audit CMASS candidates whose next step (stage2_state.py) is audit or second_read.',
  phases: [
    { title: 'State', detail: 'stage2_state.py before and after every step: what exists on disk' },
    { title: 'Audit', detail: 'one auditor per question (stage2/AUDIT.md); validate.py to 0 errors' },
    { title: 'Second read', detail: 'an independent second reader confirms or revises the audit' },
  ],
}

// args: {set: "<set>", qids: ["<qid>", ...]}   qids omitted: every question of the set whose next step is audit or second_read
//    or [{set: "<set>", qid: "<qid>"}, ...], or {items: [{set, qid}, ...]} (several sets)
// {run, tools} in args override the two path constants (revision.js passes its own).
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
  properties: { qid: STR, completed: BOOL, failure: { type: 'string', description: 'why the work could not be finished; empty when completed' }, summary: STR },
  required: ['qid', 'completed', 'failure', 'summary'],
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
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the files on disk afterwards.`

const version = (s) => `revision ${s.revision}: question "${s.question}"; answer "${s.answer_text}" (answer type ${s.answer_type}; set ${s.set}, family ${s.family}, group ${s.group})`

const auditPrompt = (s, round) => `You are the auditor (C2-C3) of question ${s.qid}, ${version(s)}.
Read ${GUIDES}/AUDIT.md in full and follow it exactly.

1. Build your packet: python3 ${TOOLS}/packets.py ${s.set} ${s.qid}  then read ${s.packet_file}.
2. Audit this version: constraints, hops, added hops, candidates by three routes at depth 50, completeness, contamination, answer check, verdict.
3. Write ${s.audit_file} (SCHEMA.md "Audit")${s.revision ? `, with "revision": ${s.revision} and exactly this version's question and answer` : ''}.${round ? `
   An earlier attempt left that file unfinished (${s.detail}): finish it, keeping what holds.` : ''}
4. Run python3 ${TOOLS}/validate.py ${s.set} ${s.qid} and fix every ERROR until it reports 0 errors.
Do not write a "verification" block: an independent second reader adds it. Touch no other file.
${COMMON}`

const secondPrompt = (s, fix) => `You are the second reader (C3) of the audit of question ${s.qid}, ${version(s)}. Another agent wrote ${s.audit_file}; you did not.
Read ${GUIDES}/AUDIT.md in full, especially "Second reader", and recheck the audit independently against the documents (packet: ${s.packet_file}).
Edit the audit in place where it is wrong (fix before you fail: search for the missing evidence first), recompute the verdict and summary, and add the "verification" block: result confirmed or revised, every change in one line.${fix ? `
The previous pass left the file unfinished (${s.detail}): finish it.` : ''}
Then run python3 ${TOOLS}/validate.py ${s.set} ${s.qid} and python3 ${TOOLS}/check_records.py ${s.set} ${s.qid} --only audit until both report 0 errors. Touch no other file.
${COMMON}`

const brief = (r) => (r ? { completed: r.completed, failure: r.failure, summary: r.summary } : null)

const runOne = async (it) => {
  let s = await stateOf(it, 'start')
  const before = s.next
  const reports = []
  if (s.next === 'error') return { ...it, before, after: 'error', detail: s.detail }
  for (let round = 0; round < 2 && s.next === 'audit'; round++) {
    reports.push(brief(await agent(auditPrompt(s, round), { label: `audit:${it.qid}`, phase: 'Audit', schema: REPORT })))
    s = await stateOf(it, `audit${round + 1}`)
  }
  for (let round = 0; round < 2 && s.next === 'second_read'; round++) {
    reports.push(brief(await agent(secondPrompt(s, round > 0), { label: `second:${it.qid}`, phase: 'Second read', schema: REPORT })))
    s = await stateOf(it, `second${round + 1}`)
    if (s.next === 'audit') {   // the second reader's edits left validate.py errors: one repair pass, then the state again
      reports.push(brief(await agent(secondPrompt(s, true), { label: `second-fix:${it.qid}`, phase: 'Second read', schema: REPORT })))
      s = await stateOf(it, `second-fix${round + 1}`)
    }
  }
  return { ...it, before, after: s.next, revision: s.revision, detail: s.detail, stage: s.stage, reason: s.reason, reports }
}

const items = await worklist(['audit', 'second_read'])
if (!items.length) {
  log('No questions to audit (pass {set, qids} or [{set, qid}], or every question of the set is past C3).')
  return { audited: 0 }
}
log(`Audit (C2-C3): ${items.length} question(s); run ${RUN}`)
const rows = (await pipeline(items, (it) => runOne(it).catch((e) => ({ ...it, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after C2-C3: ${JSON.stringify(count)}`)
const stuck = rows.filter((r) => ['audit', 'second_read', 'error'].includes(r.after))
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => `${r.set}/${r.qid}`).join(', ')}`)
return { count, rows }
