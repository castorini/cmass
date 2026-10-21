export const meta = {
  name: 'cmass-stage1-hotpotqa-gates',
  description: 'CMASS Stage 1, HotpotQA: the four gates for contamination-clean bridge questions, in order, one agent per question per gate, a question stopping at the first gate it fails: Extreme (atomize, decompose into hops, re-ground every hop on full document text with a fresh verbatim snippet), Uniqueness (adversarial search for a rival answer; is the gold answer supported), Coherence (hops agree and jointly derive the answer), Annotator (a careful reader per hop: keep, fix or drop). Each agent writes stage1/<set>/<gate>/<qid>.json until hpqa_state.py check reports 0 errors. Resumes from disk: hpqa_state.py decides every step, never an agent report.',
  whenToUse: 'Run the HotpotQA Stage 1 gates for questions whose next step (hpqa_state.py list) is stage_gates, extreme, uniqueness, coherence or annotator.',
  phases: [
    { title: 'State', detail: 'select_questions.py stage-gates, then hpqa_state.py before and after every agent' },
    { title: 'Extreme', detail: 'atomize question -> hops -> full-doc re-ground -> fresh verbatim snippets' },
    { title: 'Uniqueness', detail: 'adversarial search for a rival answer the evidence does not exclude' },
    { title: 'Coherence', detail: 'hops agree on entity, date and sense; jointly derive the gold answer' },
    { title: 'Annotator', detail: 'read each hop as a human annotator; fix or drop' },
  ],
}

// args: {set: "<set>", qids: ["<qid>", ...]}   qids omitted: every question of the set whose next step is a gate
//    or [{set, qid}, ...], or {items: [{set, qid}, ...]}; {run, tools} override the two path constants.
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'                                // the run directory (holds cmass.json), absolute
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'    // the pipeline's tools directory, absolute
const STAGE1 = TOOLS.replace(/\/tools\/?$/, '/stage1/hotpotqa')
const STATE_TOOL = `python3 ${STAGE1}/hpqa_state.py --run ${RUN}`
const GATES = ['extreme', 'uniqueness', 'coherence', 'annotator']
const TITLE = { extreme: 'Extreme', uniqueness: 'Uniqueness', coherence: 'Coherence', annotator: 'Annotator' }

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
const STAGED = {
  type: 'object',
  properties: { staged: { type: 'integer' }, ready: { type: 'integer' }, not_yet_screened: { type: 'integer' } },
  required: ['staged', 'ready', 'not_yet_screened'],
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

const stageGates = async (sid) => gated(() => agent(MECHANICAL(`python3 ${STAGE1}/select_questions.py --run ${RUN} stage-gates ${sid}`,
  '{"staged": n, "ready": n, "not_yet_screened": n}; return it'),
{ label: `stage-gates:${sid}`, phase: 'State', schema: STAGED, effort: 'low' }))

const worklist = async (nexts) => {
  if (Array.isArray(A) || A.items) return (Array.isArray(A) ? A : A.items).map((x) => ({ set: x.set, qid: String(x.qid) }))
  if (!A.set) return []
  if (A.qids && A.qids.length) return A.qids.map((q) => ({ set: A.set, qid: String(q) }))
  const r = await gated(() => agent(MECHANICAL(`${STATE_TOOL} list ${A.set} --next ${nexts.join(',')} --brief --json`,
    '{"questions": [...]}; return every question with its set, qid and next'),
  { label: `state:list:${A.set}`, phase: 'State', schema: LIST, effort: 'low' }))
  return ((r && r.questions) || []).map((x) => ({ set: x.set, qid: String(x.qid) }))
}

const COMMON = `Work in the run directory: cd ${RUN} before every command. Corpus commands are python3 ${TOOLS}/cm.py with --stage 1 --step <gate> --set <set> --qid <qid> (see the guide's "Corpus access"; searches with --center); run them one at a time (the corpus service is shared). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
Touch no file other than your output file. If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const OPENING = {
  extreme: (s) => `You perform an EXTREME-STRICTNESS grounding audit of one HotpotQA question projected onto the corpus: set ${s.set}, question ${s.qid}. Tools: Read, Bash, Write.
The bar: EVERY meaningful phrase of the question must be represented by a hop, and EVERY hop must be grounded by the full text of a corpus document. You FILTER, never repair. Verify everything yourself; trust nothing recorded.`,
  uniqueness: (s) => `You attack ONE HotpotQA question's answer: set ${s.set}, question ${s.qid}. Tools: Read, Bash, Write.
Every hop already passed individual grounding. You are testing something else: do the question's constraints, AS THE DOCUMENTS STATE THEM, single out the gold answer? Your job is to FIND A RIVAL the evidence does not exclude.`,
  coherence: (s) => `You judge whether ONE HotpotQA question's hops COHERE into a justification of its answer: set ${s.set}, question ${s.qid}. Tools: Read, Bash, Write.
Every hop already passed individual grounding and the answer survived a uniqueness attack. That is NOT what you are testing. You are testing whether the hops, read TOGETHER, use consistent content and actually reach the gold answer.`,
  annotator: (s) => `You are the human annotator for a corpus-grounded benchmark, reviewing ONE HotpotQA question end to end: set ${s.set}, question ${s.qid}. Tools: Read, Bash, Write.
Read it the way a careful person would who has never seen it before, and who will not accept hand-waving.`,
}

const gatePrompt = (gate, s, round) => `${OPENING[gate](s)}
Read ${STAGE1}/GATES.md: the sections "Question shape", "Corpus access" and "Gate ${GATES.indexOf(gate) + 1}: ${TITLE[gate]}", in full, and follow them exactly (<tools> is ${TOOLS}, <stage1> is ${STAGE1}, <set> is ${s.set}, <qid> is ${s.qid}, <gate> is ${gate}).

1. Read the gate input ${s.gate_input_file}: {qid, question, gold_answers, doc_ids, ...}. The documents cited for this question are the judged ones.${gate === 'extreme' ? '' : `
   Read the grounded hops in ${s.extreme_file}.`}
2. Do the gate's steps.
3. Write ${s.output_file}.${round ? `
   An earlier attempt left that file failing the check (${s.detail}): fix it, keeping what holds.` : ''}
4. Run ${STATE_TOOL} check ${s.set} ${s.qid} ${gate} and fix every ERROR until it reports 0 errors.
${COMMON}`

const brief = (r) => (r ? { completed: r.completed, failure: r.failure, summary: r.summary } : null)

const runOne = async (it) => {
  let s = await stateOf(it, 'start')
  const before = s.next
  const reports = []
  if (s.next === 'error') return { ...it, before, after: 'error', detail: s.detail }
  let tries = 0
  let last = ''
  while (GATES.includes(s.next)) {
    const gate = s.next
    tries = gate === last ? tries + 1 : 0
    if (tries >= 2) break                           // the same gate twice without a valid file: leave it pending
    last = gate
    const r = await gated(() => agent(gatePrompt(gate, s, tries), { label: `${gate}:${it.qid}`, phase: TITLE[gate], schema: REPORT, agentType: AGENT_TYPE }))
    reports.push({ gate, ...brief(r) })
    if (!r) break                                   // died (429, circuit open): no verdict, not a drop
    s = await stateOf(it, `${gate}${tries + 1}`)
  }
  return { ...it, before, after: s.next, stage: s.stage, detail: s.detail, reports }
}

if (A.set) {
  const st = await stageGates(A.set)
  log(`gate inputs: ${st ? JSON.stringify(st) : 'staging failed (run select_questions.py stage-gates by hand)'}`)
} else {
  for (const sid of [...new Set(((Array.isArray(A) ? A : A.items) || []).map((x) => x.set))]) await stageGates(sid)
}
const items = await worklist(GATES)
if (!items.length) {
  log('No questions for the gates (pass {set, qids}, or no clean bridge question of the set is waiting).')
  return { judged: 0 }
}
log(`HotpotQA gates: ${items.length} question(s); run ${RUN}`)
const rows = (await pipeline(items, (it) => runOne(it).catch((e) => ({ ...it, after: 'error', detail: String(e) })))).filter(Boolean)
const count = {}
for (const r of rows) {
  const k = r.after === 'dropped' ? `dropped:${r.stage}` : r.after
  count[k] = (count[k] || 0) + 1
}
log(`after the gates: ${JSON.stringify(count)} (done = survivor)`)
const stuck = rows.filter((r) => GATES.includes(r.after) || ['error', 'stage_gates'].includes(r.after))
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => `${r.set}/${r.qid}`).join(', ')}`)
return { count, rows }
