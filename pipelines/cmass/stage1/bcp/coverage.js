export const meta = {
  name: 'cmass-stage1-bcp-coverage',
  description: 'BrowseComp-Plus Stage 1, coverage audit: one agent per all-hops-grounded question (and per verified near-miss repair) checks, from the question and hop list alone, that every content phrase of the question, specific numbers included, is represented by a hop. No corpus access. Writes stage1/<set>/coverage/<qid>.json. Resumes from disk: bcp_state.py decides what is pending and validates every output.',
  whenToUse: 'After bcp_build.py coverage-bundles: audit coverage for the questions whose coverage output is missing or invalid.',
  phases: [
    { title: 'State', detail: 'bcp_state.py before and after every agent: what exists on disk' },
    { title: 'Coverage2', detail: 'every content sub-clue incl. specific numbers has a hop (COVERAGE.md)' },
  ],
}

// Ported from the private stage2/audit_coverage.js (and coverage_repaired.js / coverage_538.js, the same check on
// repaired near misses). The originals embedded each question's data in the prompt and gave the agent no tools; here
// the agent reads that data from one file and writes one file, and runs nothing else.
// args: {run, tools, set, qids}: absolute paths; qids omitted = every coverage target whose output is missing or invalid.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'
const STAGE1 = A.stage1 || TOOLS.replace(/\/tools\/?$/, '/stage1/bcp')
const SET = A.set || 'bcp'
const STEP = 'coverage'
// The original set neither a model nor an agent type: the default workflow agent with the session's model.

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
    coverage_ok: BOOL, uncovered: { type: 'array', items: STR }, summary: STR,
  },
  required: ['qid', 'completed', 'failure', 'coverage_ok', 'uncovered', 'summary'],
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

const prompt = (s, fix) => `You check ONE thing: does every information-bearing CONTENT phrase in the QUESTION have a corresponding hop? qid=${s.qid} (set ${SET}).
Judge ONLY from the question's data. Do NOT assess how well hops are grounded, only whether each content phrase is REPRESENTED by a hop.
Read ${STAGE1}/COVERAGE.md in full and follow it exactly. You may run only these two commands (from ${RUN}), read the one data file, and write the one output file; no corpus search, no other files, no outside knowledge:
  cd ${RUN} && python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --input     (prints the data file's path)
  cd ${RUN} && python3 ${STAGE1}/bcp_state.py ${SET} ${STEP} ${s.qid} --check     (must print OK)
CRITICAL - SPECIFIC NUMBERS: a number, count or interval stated in the question is itself a content sub-clue; a hop that covers the surrounding event but omits the number does NOT cover it. IGNORE "as of <year>" timestamp qualifiers.
Write ${s.output} = {qid, coverage_ok, uncovered_phrases, keep, reason}; coverage_ok = uncovered_phrases empty; keep = coverage_ok.${fix ? `
A previous attempt left that file missing or invalid (${s.detail}${s.errors && s.errors.length ? ': ' + s.errors.slice(0, 5).join('; ') : ''}): fix it.` : ''}
If you cannot finish, report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const runOne = async (qid) => {
  let s = await stateOf(qid, 'start')
  const before = s.status
  if (s.status === 'error' || s.status === 'not_a_target') return { qid, before, after: s.status, detail: s.detail }
  const reports = []
  for (let round = 0; round < 2 && (s.status === 'pending' || s.status === 'invalid'); round++) {
    const r = await agent(prompt(s, s.status === 'invalid'), { label: `cov:${qid}`, phase: 'Coverage2', schema: REPORT })
    reports.push(r ? { completed: r.completed, failure: r.failure, coverage_ok: r.coverage_ok, uncovered: r.uncovered } : null)
    s = await stateOf(qid, `after${round + 1}`)
  }
  return { qid, before, after: s.status, errors: s.errors, reports }
}

const items = await worklist()
if (!items.length) {
  log(`No coverage audits pending (pass {set, qids}; build coverage_bundles.jsonl first).`)
  return { judged: 0 }
}
log(`Coverage audit: ${items.length} question(s); run ${RUN}, set ${SET}`)
const rows = (await pipeline(items, (q) => runOne(q).catch((e) => ({ qid: q, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after coverage: ${JSON.stringify(count)} (bcp_build.py coverage-final reads the files)`)
const stuck = rows.filter((r) => r.after !== 'done')
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => r.qid).join(', ')}`)
return { count, rows }
