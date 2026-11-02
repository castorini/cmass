export const meta = {
  name: 'cmass-stage1-dsqa',
  description: 'CMASS Stage 1 for DeepSearchQA: runs one agent step of the DSQA projection (project, strict, extreme, xcheck, polish, consolidate, coherence, annotator, adjudicate or table_repair) with one agent per question, each writing exactly one output file stage1/<set>/<step>/<qid>.json from its packet, as the guide of that step says. Resumes from disk: dsqa_state.py decides what is pending and validates every output, never an agent report.',
  whenToUse: 'Run a DSQA Stage 1 agent step for the questions whose status (dsqa_state.py STEP) is pending or invalid. The deterministic steps between them are dsqa_build.py commands (README.md gives the order).',
  phases: [
    { title: 'State', detail: 'dsqa_state.py before and after every agent: what exists on disk and whether it is valid' },
    { title: 'Agent', detail: 'one agent per question for the chosen step (guides/<STEP>.md); it writes exactly one output file' },
  ],
}

// args: {step: "<step>", set: "dsqa", qids: ["<qid>", ...]}   qids omitted: every question whose status is pending or invalid
//   (xcheck needs qids: the cross-check sample is chosen by hand). {run, tools} override the two path constants;
//   {model: "<model>"} overrides the model of every agent of this run ("" = the session's model).
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'                                // the run directory (holds cmass.json and tasks/), absolute
const TOOLS = A.tools || '/path/to/cmass/pipelines/cmass/tools'    // the shared pipeline's tools directory, absolute
const HERE = TOOLS.replace(/\/tools\/?$/, '/stage1/dsqa')
const GUIDES = HERE + '/guides'
const STEP = A.step || ''
const SET = A.set || 'dsqa'

// Models, as run. The original extreme-audit workflow pinned its agents to Fable; after Fable's usage limit the pin
// was removed and the rest of that step ran on the session's model (README.md, "Who ran what"). No other original
// workflow set a model: those steps ran on the session's model.
const EXTREME_MODEL = 'fable'
const MODEL = { extreme: EXTREME_MODEL }
// Concurrency, as run: the projector ran in batches of 24 and the strict audit in batches of 16; later steps ran as a pipeline.
const BATCH = { project: 24, strict: 16 }
const ROLE = {
  project: 'the projector', strict: 'the strict auditor', extreme: 'the extreme-strictness auditor',
  xcheck: 'the extreme-strictness auditor', polish: 'the snippet polisher',
  consolidate: 'the evidence consolidator', coherence: 'the coherence judge', annotator: 'the human-annotator reader',
  adjudicate: 'the final adjudicator', table_repair: 'the table-quote repairer',
}
const GUIDE = {
  project: 'PROJECT.md', strict: 'STRICT_AUDIT.md', extreme: 'EXTREME_AUDIT.md', xcheck: 'EXTREME_AUDIT.md',
  polish: 'POLISH.md', consolidate: 'CONSOLIDATE.md', coherence: 'COHERENCE.md', annotator: 'ANNOTATOR.md',
  adjudicate: 'ADJUDICATE.md', table_repair: 'TABLE_REPAIR.md',
}

const STR = { type: 'string' }
const ROW = {
  type: 'object',
  properties: { qid: STR, status: STR, packet: STR, output: STR, errors: { type: 'array', items: STR } },
  required: ['qid', 'status', 'packet', 'output', 'errors'],
}
const STATE = {
  type: 'object',
  properties: { step: STR, set: STR, guide: STR, blocked: STR, warnings: { type: 'array', items: STR }, questions: { type: 'array', items: ROW } },
  required: ['step', 'set', 'guide', 'blocked', 'warnings', 'questions'],
}
const REPORT = {
  type: 'object',
  properties: { qid: STR, completed: { type: 'boolean' }, failure: { type: 'string', description: 'why the work could not be finished; empty when completed' }, summary: STR },
  required: ['qid', 'completed', 'failure', 'summary'],
}

const MECHANICAL = (cmd, what) => `Run exactly this one shell command and return what it prints. Do not run anything else and do not edit any file.

  cd ${RUN} && ${cmd}

It prints one JSON object ${what}. Return it as your structured output, copying every field value exactly as printed: do not summarize, correct or fill in anything.`

const stateCmd = (qids, pending) => `python3 ${HERE}/dsqa_state.py --set ${SET} ${STEP} ${qids.join(' ')}${pending ? ' --pending' : ''} --json`

const stateOf = async (qid, tag) => {
  const s = await agent(MECHANICAL(stateCmd([qid], false),
    `{"step", "set", "guide", "blocked", "warnings", "questions": [ ... ]} with exactly one question; return the whole object. If the command fails, return step "${STEP}", set "${SET}", the error text in blocked, and questions [{"qid": "${qid}", "status": "error", "packet": "", "output": "", "errors": [the error text]}]`),
  { label: `state:${qid}:${tag}`, phase: 'State', schema: STATE, effort: 'low' })
  const row = s && s.questions && s.questions[0]
  return row || { qid, status: 'error', packet: '', output: '', errors: ['the state agent returned nothing'] }
}

const COMMON = (row) => `Work in the run directory: cd ${RUN} before every command. The corpus is reached only through the shared corpus CLI, always with these flags so every search is logged for this question and step:
  python3 ${TOOLS}/cm.py search --stage 1 --step ${STEP} --set ${SET} --qid ${row.qid} "<query>" --hits 30 --chars 600 --center
  python3 ${TOOLS}/cm.py doc --stage 1 --step ${STEP} --set ${SET} --qid ${row.qid} <docid> --max 1000000
${GUIDES}/TOOLS.md says how these map onto the original agents' commands. Run corpus commands one at a time (the corpus service is shared). A failed request (an HTTP error, a timeout) is not evidence: wait and run it again.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow checks the file on disk afterwards.`

const prompt = (row, round) => `You are ${ROLE[STEP]} of DeepSearchQA question ${row.qid} (set ${SET}), Stage 1 step "${STEP}".
Read ${GUIDES}/TOOLS.md and ${GUIDES}/${GUIDE[STEP]} in full and follow them exactly.

Your input is the packet ${row.packet} (JSON). Write your result, one JSON object in the format the guide gives, to
  ${row.output}
and touch no other file. Then run  python3 ${HERE}/dsqa_state.py --set ${SET} ${STEP} ${row.qid}  : it must report the question as done. Fix every error it prints (edit your own output only) until it does.${row.status === 'invalid' ? `
An earlier attempt left that output invalid: ${(row.errors || []).join('; ') || 'see dsqa_state.py'}. Read it, keep what holds, and fix it.` : (round ? `
An earlier attempt wrote no output file: do the whole step.` : '')}
${COMMON(row)}`

const runOne = async (qid) => {
  let row = await stateOf(qid, 'start')
  const before = row.status
  const reports = []
  for (let round = 0; round < 2 && (row.status === 'pending' || row.status === 'invalid'); round++) {
    const model = A.model !== undefined ? A.model : (MODEL[STEP] || '')
    const opts = { label: `${STEP}:${qid}`, phase: 'Agent', schema: REPORT, agentType: 'general-purpose' }
    if (model) opts.model = model
    const r = await agent(prompt(row, round), opts)
    reports.push(r ? { completed: r.completed, failure: r.failure, summary: r.summary } : null)
    row = await stateOf(qid, `after${round + 1}`)
  }
  return { qid, before, after: row.status, errors: row.errors, reports }
}

if (!GUIDE[STEP]) {
  log(`Pass {"step": one of ${Object.keys(GUIDE).join(', ')}, "set": "${SET}", "qids": [...]}.`)
  return { done: 0 }
}
let items = (A.qids || []).map(String)
if (!items.length) {
  if (STEP === 'xcheck') {
    log('xcheck has no default worklist: pass the sampled qids (README.md, step 4).')
    return { done: 0 }
  }
  const s = await agent(MECHANICAL(stateCmd([], true), '{"step", "set", "guide", "blocked", "warnings", "questions": [...]}; return it whole'),
    { label: `state:list:${STEP}`, phase: 'State', schema: STATE, effort: 'low' })
  if (s && s.blocked) log(`blocked: ${s.blocked}`)
  for (const w of (s && s.warnings) || []) log(`warning: ${w}`)
  items = ((s && s.questions) || []).map((x) => String(x.qid))
}
if (!items.length) {
  log(`Nothing to do for ${STEP} (every eligible question is done, or the step is blocked).`)
  return { done: 0 }
}
log(`DSQA Stage 1, step ${STEP}: ${items.length} question(s); run ${RUN}`)
const safe = (q) => runOne(q).catch((e) => ({ qid: q, after: 'error', errors: [String(e)] }))
let rows = []
if (BATCH[STEP]) {
  for (let i = 0; i < items.length; i += BATCH[STEP]) {
    const b = items.slice(i, i + BATCH[STEP])
    rows.push(...(await parallel(b.map((q) => () => safe(q)))).filter(Boolean))
    log(`batch ${Math.floor(i / BATCH[STEP]) + 1}/${Math.ceil(items.length / BATCH[STEP])}: ${rows.filter((r) => r.after === 'done').length} done`)
  }
} else {
  rows = (await pipeline(items, safe)).filter(Boolean)
}
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after ${STEP}: ${JSON.stringify(count)}`)
const stuck = rows.filter((r) => r.after !== 'done')
if (stuck.length) log(`not finished (rerun them): ${stuck.map((r) => r.qid).join(', ')}`)
return { step: STEP, count, rows }
