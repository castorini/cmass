export const meta = {
  name: 'cmass-stage3-rules',
  description: 'CMASS Stage 3 corpus-scan rules: one agent per question writes the match rules of every hop with qrels (stage3/RULES.md) until rules.py state says done: each hop\'s rules match 100% of its known qrels documents and its group stays within the breadth cap, estimated on a sample of shards, or carries a broad_ok reason. Resumes from disk: every step is decided by rules.py state, never by an agent report.',
  whenToUse: 'After Stage 3 qrels exist (qrels_hops.jsonl) and before scan.py scan; args {run, tools, set, qids?, config?}. Then rules.py collect writes the --rules of the scan.',
  phases: [
    { title: 'State', detail: 'rules.py state before and after every agent: what exists on disk' },
    { title: 'Rules', detail: 'one agent per question: packet, rules, check to 100%, breadth within the cap' },
  ],
}

// args: {run: "<run dir>", tools: "<checkout>/pipelines/cmass/stage3", set: "<set>", qids?: ["<qid>", ...],
//        config?: "<rules.py config JSON>", python?: "python3"}
//   run     absolute; every command runs there (cd run), so relative paths in config are fine
//   tools   the checkout's pipelines/cmass/stage3 (a pipelines/cmass/tools or pipelines/cmass path is mapped to it)
//   qids    omitted: every question of the set whose state is neither done nor skip
//   config  the rules.py config file (rules.py docstring: qrels_hops, corpus, out, max_est, ...), relative to run;
//           default rules_config.json. args.set is passed as --set.
//   python  the interpreter for rules.py (one with pyahocorasick makes breadth several times faster); default python3
const A = (typeof args === 'string' ? JSON.parse(args) : args) || {}
const RUN = A.run || '/path/to/run'
const T0 = (A.tools || '/path/to/cmass/pipelines/cmass/stage3').replace(/\/+$/, '')
const STAGE3 = /\/stage3$/.test(T0) ? T0 : /\/tools$/.test(T0) ? T0.replace(/\/tools$/, '/stage3') : `${T0}/stage3`
const SET = A.set
const CONFIG = A.config || 'rules_config.json'
const R = `${A.python || 'python3'} ${STAGE3}/rules.py --config ${CONFIG} --set ${SET}`
const MAX_ROUNDS = 3        // rule-writing agents per question; each loops check/breadth itself

const STR = { type: 'string' }
const STRS = { type: 'array', items: STR }
const INT = { type: 'integer' }
const BOOL = { type: 'boolean' }
const HOP_FIELDS = {
  hop: STR, group: STR, known: INT, rules: INT, matched: INT, missed: STRS, unavailable: STRS, passed: BOOL,
  est: INT, cap_ok: BOOL, broad_ok: STR,
}
const HOP = { type: 'object', properties: HOP_FIELDS, required: Object.keys(HOP_FIELDS) }
const STATE_FIELDS = {
  set: STR, qid: STR, next: STR, done: BOOL, detail: STR, max_est: INT, packet_file: STR, rules_file: STR,
  check_file: STR, breadth_file: STR, hops: { type: 'array', items: HOP },
}
const STATE = { type: 'object', properties: STATE_FIELDS, required: Object.keys(STATE_FIELDS) }
const LIST = {
  type: 'object',
  properties: { questions: { type: 'array', items: { type: 'object', properties: { qid: STR, next: STR }, required: ['qid', 'next'] } } },
  required: ['questions'],
}
const REPORT = {
  type: 'object',
  properties: {
    qid: STR, completed: { type: 'boolean', description: 'true only if the last rules.py state printed next "done"' },
    failure: { type: 'string', description: 'what remains (missed ids, groups over the cap) or why the work could not be finished; empty when completed' },
    summary: STR,
  },
  required: ['qid', 'completed', 'failure', 'summary'],
}

const MECHANICAL = (cmd, what) => `Run exactly this one shell command and return what it prints. Do not run anything else and do not edit any file.

  cd ${RUN} && ${cmd}

It prints one JSON object ${what}. Return it as your structured output, copying every field value exactly as printed: do not summarize, correct or fill in anything.`

const errorState = (qid, detail) => ({ set: SET, qid, next: 'error', done: false, detail, hops: [] })

const stateOf = async (qid, tag) => {
  const s = await agent(MECHANICAL(`${R} state --json ${qid}`,
    `{"set": ..., "questions": [ ... ], "count": {...}} whose "questions" holds exactly one question object; return that one object. If the command fails or prints no question, return qid "${qid}", next "error", the error text in detail, and empty values (0, false, "", []) for every other field`),
  { label: `state:${qid}:${tag}`, phase: 'State', schema: STATE, effort: 'low' })
  if (!s) return errorState(qid, 'the state agent returned nothing')
  if (String(s.qid) !== qid) return errorState(qid, `the state agent returned question ${s.qid}`)
  return s
}

const worklist = async () => {
  if (A.qids && A.qids.length) return A.qids.map(String)
  const r = await agent(MECHANICAL(`${R} state --json --brief --todo`,
    '{"set": ..., "questions": [...], "count": {...}}; return every question of "questions" with its qid and next'),
  { label: `state:list:${SET}`, phase: 'State', schema: LIST, effort: 'low' })
  return ((r && r.questions) || []).filter((x) => !['done', 'skip'].includes(x.next)).map((x) => String(x.qid))
}

const COMMON = `Work in the run directory: cd ${RUN} before every command. Every tool command is ${R} <command> (Bash timeout 600000).
Documents are read from the local corpus by the tool (packet, doc, check); never use a search service. Run one breadth at a time; it may print "waiting for a breadth slot" while other questions use them: let it wait.
Touch only this question's rules file. The packet, known, check and breadth files are written by the tool: never edit them, and never touch another question's files.
If a tool call is refused or fails so that you cannot finish, stop and report completed=false with the reason in failure. Never claim success for work you did not complete: the workflow reads rules.py state afterwards.`

const hopLine = (h) => `  hop ${h.hop} (group ${h.group}): ${h.rules} rule(s); matched ${h.matched < 0 ? '?' : h.matched}/${h.known}`
  + (h.missed.length ? `, missed ${h.missed.join(' ')}` : '') + (h.unavailable.length ? `, unavailable ${h.unavailable.join(' ')}` : '')
  + `; est ${h.est < 0 ? '?' : h.est}${h.broad_ok ? ' (broad_ok)' : ''}`

const rulesPrompt = (s, round) => `You write the corpus-scan match rules of question ${s.qid} of set ${SET}. Read ${STAGE3}/RULES.md in full first and follow it exactly.

State now (rules.py state, from disk): next "${s.next}": ${s.detail}
${s.hops.map(hopLine).join('\n')}${round ? `
An earlier attempt stopped before the state said done. Continue from the files on disk, keeping the rules that hold.` : ''}

1. Run ${R} packet ${s.qid}, then read ${s.packet_file} in full.
2. Write ${s.rules_file} (or fix it, if it exists): for every hop of the packet, rules whose "group" is the hop's group and whose "rule_id" starts with "<group>/", as RULES.md describes.
3. Run ${R} check ${s.qid}. Until every hop passes at 100%: look at each missed document (${R} doc ${s.qid} DOCID --grep PHRASE ...), then add the variant it uses or a rule of its own; check again. 100% is required: never drop a known document.
4. Run ${R} breadth ${s.qid}. For every group over the cap (${s.max_est} documents): narrow its rules (RULES.md, "Breadth"), check again (step 3), then breadth again. Use "meta": {"broad_ok": "<reason>"} only as RULES.md allows.
5. Run ${R} state ${s.qid}, and repeat from step 2 until it prints next "done". After six check-and-breadth rounds without reaching done, stop and report what remains.

Return qid, completed (true only if the last state printed next "done"), failure (otherwise what remains: missed ids per hop, groups over the cap with their estimates) and a summary: per hop, how many rules, which known documents needed a rule of their own, and any broad_ok with its reason.
${COMMON}`

const brief = (r) => (r ? { completed: r.completed, failure: r.failure, summary: r.summary } : null)
const hopSummary = (h) => ({ hop: h.hop, rules: h.rules, matched: h.matched, known: h.known, missed: h.missed, unavailable: h.unavailable, est: h.est, cap_ok: h.cap_ok, broad_ok: h.broad_ok })

const runOne = async (qid) => {
  let s = await stateOf(qid, 'start')
  const before = s.next
  const reports = []
  for (let round = 0; round < MAX_ROUNDS && !s.done && !['error', 'skip'].includes(s.next); round++) {
    reports.push(brief(await agent(rulesPrompt(s, round), { label: `rules:${qid}:${round + 1}`, phase: 'Rules', schema: REPORT })))
    s = await stateOf(qid, `round${round + 1}`)
  }
  return { qid, before, after: s.next, done: !!s.done, detail: s.detail, hops: (s.hops || []).map(hopSummary), reports }
}

if (!SET) { log('Pass args {run, tools, set, qids?, config?}: no set given.'); return { done: 0 } }
if (RUN.startsWith('/path/to') || STAGE3.startsWith('/path/to')) { log('Pass absolute {run, tools} in args.'); return { done: 0 } }
const items = await worklist()
if (!items.length) {
  log(`No questions to write rules for (pass {qids}, or every question of set ${SET} is done or skip).`)
  return { done: 0 }
}
log(`Rules: ${items.length} question(s) of set ${SET}; run ${RUN}; state from rules.py`)
const rows = (await pipeline(items, (q) => runOne(q).catch((e) => ({ qid: q, after: 'error', done: false, detail: String(e), hops: [] })))).filter(Boolean)
const count = {}
for (const r of rows) count[r.after] = (count[r.after] || 0) + 1
log(`after: ${JSON.stringify(count)}`)
const stuck = rows.filter((r) => !r.done && r.after !== 'skip')
if (stuck.length) log(`NOT done (rules.py state; rerun them or fix by hand): ${stuck.map((r) => `${r.qid} [${r.after}] ${r.detail}`).join(' | ')}`)
log(`when every question is done: cd ${RUN} && ${R} collect --out-rules <passing.jsonl>  (the --rules of scan.py scan)`)
return { count, done: rows.filter((r) => r.done).length, not_done: stuck.map((r) => r.qid), rows }
