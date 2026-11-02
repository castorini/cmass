export const meta = {
  name: 'cmass-stage2-adversarial',
  description: 'CMASS Stage 2, C5 adversarial check: per question whose audit passes, independent hunters (two with complementary routes for aggregates and changed answers) look for rivals, missing or wrong members, conflicting values and alternative readings; then a separate verifier rules on every finding. One output file per agent; resumes from disk (tools/stage2_state.py).',
  whenToUse: 'Challenge CMASS questions whose next step (stage2_state.py) is hunt or verify.',
  phases: [
    { title: 'State', detail: 'stage2_state.py before and after every step: what exists on disk' },
    { title: 'Hunt', detail: 'hunters try to break the question (stage2/ADVERSARIAL.md), one file each' },
    { title: 'Verify', detail: 'a separate verifier reads the cited passages in context and rules on every finding' },
  ],
}

// args: {set: "<set>", qids: ["<qid>", ...]}   qids omitted: every question of the set whose next step is hunt or verify
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

const version = (s) => `revision ${s.revision}: question "${s.question}"; answer "${s.answer_text}" (answer type ${s.answer_type}; set ${s.set}, family ${s.family})`

const ROUTE = {
  hunt: 'all: rival answers, missing or wrongly included members, conflicting values, alternative readings and ungrounded hops, by list documents, named candidates and category descriptions',
  hunt_list: 'list: list, table, ranking, catalogue and category documents of the domain, every listed entity checked against every constraint (another hunter takes named candidates)',
  hunt_named: 'named: every candidate you know of or meet, searched by name (other spellings, predecessors, successors, renamed entities) and checked against every constraint (another hunter takes list documents)',
}
const KIND_ROUTE = { hunt: 'all', hunt_list: 'list', hunt_named: 'named' }

const huntPrompt = (s, h, fix) => `You are an adversarial hunter (C5) for question ${s.qid}, ${version(s)}. Your job is to break it.
Read ${GUIDES}/ADVERSARIAL.md ("Hunter") and ${GUIDES}/AUDIT.md (tools, grounding rules, shared requirements) first.
The audit: ${s.audit_file} (read its candidates, exclusion hops, completeness and notes, and run cm.py history, so you search differently). The packet: ${s.packet_file}.
Your route: ${ROUTE[h.kind]}. Search with --hits 50 and reformulate; web search may give leads, never evidence; quotes come from cm.py quote.
Write your findings to ${h.path} with "kind": "${h.kind}", "route": "${KIND_ROUTE[h.kind]}", "revision": ${s.revision}, and exactly this version's question and answer (SCHEMA.md "Hunter findings"). Empty findings are a valid result.${fix ? `
Your file has errors: ${fix.join(' | ')}. Fix them.` : ''}
Then run python3 ${TOOLS}/check_records.py ${s.set} ${s.qid} --only hunt until it reports no error about your file. Do not edit the audit or any other file.
${COMMON}`

const verifyPrompt = (s, fix) => `You are the verifier (C5) for question ${s.qid}, ${version(s)}.
Read ${GUIDES}/ADVERSARIAL.md ("Verifier") and ${GUIDES}/AUDIT.md (tools, grounding rules, shared requirements) first.
Hunter files: ${s.hunts.map((h) => h.path).join(', ')}. The audit: ${s.audit_file}.
Rule on every finding, refutes or does_not_refute, after reading the cited passages in context (cm.py doc --grep; whole documents when needed) and the audit's hops and candidates. Add record_exclusion when you find the stated fact that excludes a candidate the audit does not record.
Write ${s.verify_file} with "revision": ${s.revision} and "hunts": ${JSON.stringify(s.hunts.map((h) => h.kind))} (SCHEMA.md "Verifier rulings").${fix ? `
Your file has errors: ${fix.join(' | ')}. Fix them.` : ''}
Then run python3 ${TOOLS}/check_records.py ${s.set} ${s.qid} --only verify until it reports 0 errors. Do not edit the audit or any other file.
${COMMON}`

const brief = (r) => (r ? { completed: r.completed, failure: r.failure, summary: r.summary } : null)
const errorsAbout = (s, name) => (s.record_errors || []).filter((e) => e.includes(` ${name}.r${s.revision}`))

const runOne = async (it) => {
  let s = await stateOf(it, 'start')
  const before = s.next
  const reports = []
  if (s.next === 'error') return { ...it, before, after: 'error', detail: s.detail }
  if (s.next === 'hunt') {
    const todo = s.hunts.filter((h) => !h.done)
    reports.push(...(await parallel(todo.map((h) => () =>
      agent(huntPrompt(s, h), { label: `${h.kind}:${it.qid}`, phase: 'Hunt', schema: REPORT })))).map(brief))
    s = await stateOf(it, 'hunted')
    const bad = s.hunts.filter((h) => h.done && errorsAbout(s, h.kind).length)
    if (bad.length) {
      reports.push(...(await parallel(bad.map((h) => () =>
        agent(huntPrompt(s, h, errorsAbout(s, h.kind)), { label: `${h.kind}-fix:${it.qid}`, phase: 'Hunt', schema: REPORT })))).map(brief))
      s = await stateOf(it, 'hunt-fixed')
    }
    if (s.hunts.some((h) => errorsAbout(s, h.kind).length)) return { ...it, before, after: 'hunt_errors', detail: s.record_errors.join(' | '), reports }
  }
  if (s.next === 'verify') {
    reports.push(brief(await agent(verifyPrompt(s), { label: `verify:${it.qid}`, phase: 'Verify', schema: REPORT })))
    s = await stateOf(it, 'verified')
    if (errorsAbout(s, 'verify').length) {
      reports.push(brief(await agent(verifyPrompt(s, errorsAbout(s, 'verify')), { label: `verify-fix:${it.qid}`, phase: 'Verify', schema: REPORT })))
      s = await stateOf(it, 'verify-fixed')
    }
    if (errorsAbout(s, 'verify').length) return { ...it, before, after: 'verify_errors', detail: s.record_errors.join(' | '), reports }
  }
  return { ...it, before, after: s.next, revision: s.revision, detail: s.detail, stage: s.stage, reason: s.reason, reports }
}

const items = await worklist(['hunt', 'verify'])
if (!items.length) {
  log('No questions to challenge (pass {set, qids} or [{set, qid}], or no question of the set is waiting for C5).')
  return { checked: 0 }
}
log(`Adversarial check (C5): ${items.length} question(s); run ${RUN}`)
const rows = (await pipeline(items, (it) => runOne(it).catch((e) => ({ ...it, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after C5: ${JSON.stringify(count)} (revise = a verified refutation, sent to revision.js; review = ready for the human reviewer)`)
const stuck = rows.filter((r) => ['hunt', 'verify', 'hunt_errors', 'verify_errors', 'error'].includes(r.after))
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => `${r.set}/${r.qid}`).join(', ')}`)
return { count, rows }
