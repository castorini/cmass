export const meta = {
  name: 'cmass-stage1-hotpotqa-contamination',
  description: 'CMASS Stage 1, HotpotQA: the contamination screen. One agent per question reads its candidate documents and judges (a) leakage - a single document poses the question and gives the gold answer - and (b) single-document answerability - one document alone carries every fact the question needs - writing stage1/<set>/contamination/<qid>.json until hpqa_state.py check reports 0 errors. Resumes from disk: hpqa_state.py decides every step, never an agent report.',
  whenToUse: 'Screen HotpotQA Stage 1 questions whose next step (hpqa_state.py list) is contamination.',
  phases: [
    { title: 'State', detail: 'hpqa_state.py before and after every agent: what exists on disk' },
    { title: 'Screen', detail: 'one agent per question (CONTAMINATION.md): leakage and single-document answerability' },
  ],
}

// args: {set: "<set>", qids: ["<qid>", ...]}   qids omitted: every question of the set whose next step is contamination
//    or [{set, qid}, ...], or {items: [{set, qid}, ...]}; {run, tools} override the two path constants.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'                                // the run directory (holds cmass.json), absolute
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'    // the pipeline's tools directory, absolute
const STAGE1 = TOOLS.replace(/\/tools\/?$/, '/stage1/hotpotqa')
const STATE_TOOL = `python3 ${STAGE1}/hpqa_state.py --run ${RUN}`

// As in the original workflow: agents of the general-purpose type, no model set (the session's model ran).
const AGENT_TYPE = 'general-purpose'
// From batch 3 on, the original capped agents in flight at 4 (the API allowed 5 requests in flight per
// user; past that, agents died on 429 and left no verdict) and stopped launching after 4 consecutive deaths.
const MAX_INFLIGHT = 4
const TRIP_AFTER = 4

let inflight = 0
let deaths = 0
let tripped = false
const waiting = []
async function gated(fn) {
  while (inflight >= MAX_INFLIGHT) await new Promise((r) => waiting.push(r))
  if (tripped) { const next = waiting.shift(); if (next) next(); return null }
  inflight++
  try {
    let r = null
    try { r = await fn() } catch (e) { r = null }
    deaths = r ? 0 : deaths + 1
    if (deaths >= TRIP_AFTER && !tripped) {
      tripped = true
      log(`CIRCUIT OPEN: ${deaths} agents died in a row; launching no more. Rerun this workflow later: died is not dropped.`)
    }
    return r
  } finally { inflight--; const next = waiting.shift(); if (next) next() }
}

const STR = { type: 'string' }
const STATE_FIELDS = {
  set: STR, qid: STR, origin: STR, batch: { type: 'integer' }, hotpot_type: STR, next: STR, stage: STR, detail: STR,
  errors: { type: 'array', items: STR }, input_file: STR, gate_input_file: STR, output_file: STR, extreme_file: STR,
}
const STATE = { type: 'object', properties: STATE_FIELDS, required: Object.keys(STATE_FIELDS) }
const LIST = {
  type: 'object',
  properties: { questions: { type: 'array', items: { type: 'object', properties: { set: STR, qid: STR, next: STR }, required: ['set', 'qid', 'next'] } } },
  required: ['questions'],
}
const REPORT = {
  type: 'object',
  properties: { qid: STR, completed: { type: 'boolean' }, failure: { type: 'string', description: 'why the work could not be finished; empty when completed' }, summary: STR },
  required: ['qid', 'completed', 'failure', 'summary'],
}

const MECHANICAL = (cmd, what) => `Run exactly this one shell command and return what it prints. Do not run anything else and do not edit any file.

  cd ${RUN} && ${cmd}

It prints one JSON object ${what}. Return it as your structured output, copying every field value exactly as printed: do not summarize, correct or fill in anything.`

const stateOf = async (it, tag) => {
  const s = await gated(() => agent(MECHANICAL(`${STATE_TOOL} list --json ${it.set} ${it.qid}`,
    `{"questions": [ ... ]} with exactly one question object; return that one object. If the command fails or prints no question, return set "${it.set}", qid "${it.qid}", next "error", the error text in detail, and empty values (0, "", []) for every other field`),
  { label: `state:${it.qid}:${tag}`, phase: 'State', schema: STATE, effort: 'low' }))
  return s || { set: it.set, qid: it.qid, next: 'error', detail: 'the state agent returned nothing', errors: [] }
}

const worklist = async (nexts) => {
  if (Array.isArray(A) || A.items) return (Array.isArray(A) ? A : A.items).map((x) => ({ set: x.set, qid: String(x.qid) }))
  if (!A.set) return []
  if (A.qids && A.qids.length) return A.qids.map((q) => ({ set: A.set, qid: String(q) }))
  const r = await gated(() => agent(MECHANICAL(`${STATE_TOOL} list ${A.set} --next ${nexts.join(',')} --brief --json`,
    '{"questions": [...]}; return every question with its set, qid and next'),
  { label: `state:list:${A.set}`, phase: 'State', schema: LIST, effort: 'low' }))
  return ((r && r.questions) || []).map((x) => ({ set: x.set, qid: String(x.qid) }))
}

const COMMON = `Work in the run directory: cd ${RUN} before every command. Corpus commands are python3 ${TOOLS}/cm.py with --stage 1 --step contamination --set <set> --qid <qid> (see the guide); run them one at a time (the corpus service is shared). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
Touch no file other than your output file. If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const screenPrompt = (s, round) => `You screen ONE HotpotQA question, projected onto the corpus, for CONTAMINATION: set ${s.set}, question ${s.qid}. Tools: Read, Bash, Write.
This runs BEFORE any quality filtering. You are not judging whether the question is well grounded - only whether it is spoiled in one of two specific ways.
Read ${STAGE1}/CONTAMINATION.md in full and follow it exactly (<tools> is ${TOOLS}, <stage1> is ${STAGE1}, <set> is ${s.set}, <qid> is ${s.qid}).

1. Read the input ${s.input_file}: {qid, question, gold_answers, doc_ids}.
2. Judge leakage, then single-document answerability, then report hops_needed and distinct_docs_needed, as the guide says.
3. Write ${s.output_file}.${round ? `
   An earlier attempt left that file failing the check (${s.detail}): fix it, keeping what holds.` : ''}
4. Run ${STATE_TOOL} check ${s.set} ${s.qid} contamination and fix every ERROR until it reports 0 errors.
${COMMON}`

const brief = (r) => (r ? { completed: r.completed, failure: r.failure, summary: r.summary } : null)

const runOne = async (it) => {
  let s = await stateOf(it, 'start')
  const before = s.next
  const reports = []
  if (s.next === 'error') return { ...it, before, after: 'error', detail: s.detail }
  for (let round = 0; round < 2 && s.next === 'contamination'; round++) {
    const r = await gated(() => agent(screenPrompt(s, round), { label: `cx:${it.qid}`, phase: 'Screen', schema: REPORT, agentType: AGENT_TYPE }))
    reports.push(brief(r))
    if (!r) break                                   // died (429, circuit open): no verdict, not a drop
    s = await stateOf(it, `screen${round + 1}`)
  }
  return { ...it, before, after: s.next, stage: s.stage, detail: s.detail, reports }
}

const items = await worklist(['contamination'])
if (!items.length) {
  log('No questions to screen (pass {set, qids}, or every selected question of the set is past the screen).')
  return { screened: 0 }
}
log(`HotpotQA contamination screen: ${items.length} question(s); run ${RUN}`)
const rows = (await pipeline(items, (it) => runOne(it).catch((e) => ({ ...it, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after the screen: ${JSON.stringify(count)} (stage_gates = clean bridge, dropped = leaked or single-document, excluded = clean comparison)`)
const stuck = rows.filter((r) => ['contamination', 'error'].includes(r.after))
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => `${r.set}/${r.qid}`).join(', ')}`)
return { count, rows }
