export const meta = {
  name: 'cmass-stage2-revision',
  description: 'CMASS Stage 2, C4: one repair attempt per trigger (a failed audit, a verified refutation, a reviewer\'s accept-with-fixes, a failed final check): evidence retry first, then the least extensive permitted revision; the repaired version is then re-audited with a second reader (audit.js) and re-challenged (adversarial.js). An unsuccessful repair, a failed re-audit or a second verified refutation excludes the question. Resumes from disk (tools/stage2_state.py).',
  whenToUse: 'Repair CMASS questions whose next step (stage2_state.py) is revise.',
  phases: [
    { title: 'State', detail: 'stage2_state.py before and after every step: what exists on disk' },
    { title: 'Revise', detail: 'one repair attempt per question (stage2/REVISION.md): revisions/<set>/<qid>.r<N>.json' },
    { title: 'Re-audit', detail: 'audit.js on the repaired version (auditor + second reader), then adversarial.js (hunters + verifier)' },
  ],
}

// args: {set: "<set>", qids: ["<qid>", ...]}   qids omitted: every question of the set whose next step is revise
//    or [{set: "<set>", qid: "<qid>"}, ...], or {items: [{set, qid}, ...]} (several sets)
// audit.js and adversarial.js must sit next to this script (GUIDES); they get this script's RUN and TOOLS.
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
    outcome: { type: 'string', enum: ['evidence_only', 'revised', 'unsuccessful', 'not_written'] }, summary: STR,
  },
  required: ['qid', 'completed', 'failure', 'outcome', 'summary'],
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
If a tool call is refused or fails so that you cannot finish, stop and report completed=false (outcome not_written if you wrote no file) with the reason in failure. Never claim success for work you did not complete: the workflow checks the files on disk afterwards.`

const ANSWER_RULE = {
  yes: 'The answer may change only by recompute_answer, under the completeness standard of REVISION.md (with completeness_note).',
  no: 'The answer must not change.',
  typo_only: 'The answer must not change, except a typo correction (correct_answer_typo).',
}

const revisePrompt = (s, fix) => `You make repair attempt ${s.attempt} (C4) for question ${s.qid} of set ${s.set} (family ${s.family}, group ${s.group}).
Read ${GUIDES}/REVISION.md in full, and ${GUIDES}/AUDIT.md for the tools, grounding rules and shared requirements.

The version to repair, revision ${s.revision}: question "${s.question}"; answer "${s.answer_text}" (answer type ${s.answer_type}).
What asks for the repair: the ${s.trigger.stage} step (${s.trigger.ref}):
${s.trigger.reasons.map((r, i) => `  ${i + 1}. ${r}`).join('\n')}
Read that file${s.trigger.stage === 'human' ? ` (here: the reviewer's comments above; the full review is under "${s.trigger.ref}" in ${s.reviews_file})` : ''}, the audit ${s.audit_file}, and your packet (python3 ${TOOLS}/packets.py ${s.set} ${s.qid}, then ${s.packet_file}).

${s.trigger.evidence_only
    ? 'This trigger allows an evidence-only repair only (the final check found grounding gaps): the outcome is evidence_only or unsuccessful, with no edits.'
    : `Permitted revisions for this set: ${s.permitted.join(', ')}. ${ANSWER_RULE[s.answer_may_change] || ANSWER_RULE.no}`}
First retry the evidence (new queries, deep sweeps, rereading the documents); only if that cannot resolve every problem, make the least extensive permitted edit; if nothing within the rules works, record the attempt as unsuccessful with the blocker.

Write ${s.revision_file} (SCHEMA.md "Revision attempt") with "attempt": ${s.attempt}, "based_on_revision": ${s.revision} and "trigger": ${JSON.stringify(s.trigger)}.${fix ? `
The file has errors: ${fix.join(' | ')}. Fix them.` : ''}
Then run python3 ${TOOLS}/check_records.py ${s.set} ${s.qid} --only revision until it reports 0 errors. Do not edit the audit or any other file: the repaired version is audited again from scratch afterwards.
${COMMON}`

const brief = (r) => (r ? { completed: r.completed, failure: r.failure, outcome: r.outcome, summary: r.summary } : null)
const revErrors = (s, n) => (s.record_errors || []).filter((e) => e.includes(` r${n}:`) || e.includes(' revisions:'))

const repairOnce = async (it, s0, reports) => {
  reports.push(brief(await agent(revisePrompt(s0), { label: `revise:${it.qid}:r${s0.attempt}`, phase: 'Revise', schema: REPORT })))
  let s = await stateOf(it, `revised-r${s0.attempt}`)
  if (revErrors(s, s0.attempt).length) {
    reports.push(brief(await agent(revisePrompt(s0, revErrors(s, s0.attempt)), { label: `revise-fix:${it.qid}:r${s0.attempt}`, phase: 'Revise', schema: REPORT })))
    s = await stateOf(it, `revise-fixed-r${s0.attempt}`)
  }
  if (revErrors(s, s0.attempt).length) return { s, after: 'revision_errors', detail: s.record_errors.join(' | ') }
  if (s.next === 'revise' && s.revision === s0.revision) return { s, after: 'revise', detail: 'no repair attempt was written' }
  if (s.next === 'excluded') return { s }
  // the repaired version repeats C1-C3 and the adversarial check (C5) before it goes back to the reviewer
  const pass = { set: it.set, qids: [it.qid], run: RUN, tools: TOOLS }
  await workflow({ scriptPath: `${GUIDES}/audit.js` }, pass)
  s = await stateOf(it, `reaudited-r${s.revision}`)
  if (s.next === 'hunt' || s.next === 'verify') {
    await workflow({ scriptPath: `${GUIDES}/adversarial.js` }, pass)
    s = await stateOf(it, `rechallenged-r${s.revision}`)
  }
  return { s }
}

const runOne = async (it) => {
  let s = await stateOf(it, 'start')
  if (s.next !== 'revise') return { ...it, before: s.next, after: s.next, detail: s.detail, skipped: true }
  const reports = []
  const triggers = []
  let r = null
  // one attempt per trigger; a first verified refutation of the repaired version permits one more attempt here
  // (a second refutation is an exclusion, which stage2_state.py reports)
  for (let k = 0; k < 2 && s.next === 'revise' && (k === 0 || s.trigger.stage === 'adversarial'); k++) {
    triggers.push(`${s.trigger.stage} -> r${s.attempt}`)
    r = await repairOnce(it, s, reports)
    s = r.s
    if (r.after) break
  }
  return { ...it, before: 'revise', after: (r && r.after) || s.next, revision: s.revision, stage: s.stage, reason: s.reason,
    detail: (r && r.detail) || s.detail, triggers, reports }
}

const items = await worklist(['revise'])
if (!items.length) {
  log('No questions to repair (pass {set, qids} or [{set, qid}], or no question of the set has next step revise).')
  return { repaired: 0 }
}
log(`Revision (C4): ${items.length} question(s), one repair attempt each, then re-audit and re-challenge; run ${RUN}`)
const rows = (await pipeline(items, (it) => runOne(it).catch((e) => ({ ...it, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after C4: ${JSON.stringify(count)} (review = repaired and ready for the reviewer; revise = refuted again or another trigger; excluded = repair unsuccessful, re-audit failed or second refutation)`)
const skipped = rows.filter((r) => r.skipped)
if (skipped.length) log(`skipped (next step is not revise): ${skipped.map((r) => `${r.set}/${r.qid} (${r.after})`).join(', ')}`)
return { count, rows }
