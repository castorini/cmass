export const meta = {
  name: 'cmass-stage3-qrels-phase1',
  description: 'CMASS Stage 3 phase 1 (precision filter), one question per pipeline item: a strong model writes the hop standards and recall query families, a cheaper model judges every candidate chunk, a stronger blind second judge re-judges a policy sample (every partial-only keep, a share of the full keeps and drops, every dropped Stage 1 document; every drop when sampled drops flip), an adjudicator decides each disagreement. Resumes from disk (qrels_state.py), never from agent reports; one output file per unit of work.',
  whenToUse: 'After qrels.py pool (and fetch) for a set; args {set, qids}.',
  phases: [
    { title: 'Prepare', detail: 'one agent per question: hop standards, recall query families, recall, chunks' },
    { title: 'Judge', detail: 'one agent per chunk; a chunk whose verdicts fail the check is re-judged by the stronger model' },
    { title: 'Second judge', detail: 'blind, one agent per sample set; rounds and escalation come from qrels_state.py' },
    { title: 'Adjudicate', detail: 'one agent per disagreement part, the two verdicts shown as A and B' },
  ],
}

// ------------------------------------------------------------------ paths (point them at your checkout and run)
const PIPELINE = '/path/to/cmass/pipelines/cmass'   // this repository's pipelines/cmass directory
const RUN_DIR = '/path/to/run'                      // the run directory (cmass.json, final/, qrels/)
// ------------------------------------------------------------------ models
const MODEL_PREPARE = 'sonnet'     // hop standards and recall query families (the strong model)
const MODEL_JUDGE = 'haiku'        // first judge of every candidate chunk (the cheaper model)
const MODEL_RETRY = 'sonnet'       // second attempt at a chunk whose verdicts failed the check
const MODEL_SECOND = 'sonnet'      // blind second judge
const MODEL_ADJUDICATE = 'sonnet'  // decides each disagreement
const MODEL_HELPER = 'haiku'       // runs one deterministic command and reports what it printed
const MAX_ROUNDS = 6               // second-judge round steps per question (round 1, escalation, back-fills)

// args: {set: "<set>", qids: ["<qid>", ...], run?: RUN_DIR, pipeline?: PIPELINE}. The tiering parameters (sample
// rates, escalation threshold) are read from the run config by qrels.py and qrels_state.py, not passed here.
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const SET = A.set
const QIDS = A.qids || []
const PIPE = A.pipeline || PIPELINE
const RUN = A.run || RUN_DIR
const T = `cd ${RUN} && python3 ${PIPE}/stage3/qrels.py --set ${SET}`
const STATE = `cd ${RUN} && python3 ${PIPE}/stage3/qrels_state.py --set ${SET}`
const GUIDE = `${PIPE}/stage3/QRELS.md`
const QD = `${RUN}/qrels/${SET}`
const WK = `${QD}/work`
const JD = `${QD}/judged`
const RULES = 'If any tool call is refused or fails so that you cannot finish, stop and report failure: set check_errors to -1 (never 0 for unfinished work) and say in note what failed. Never report success for work you did not finish. Do not kill any process.'

const PREP = {
  type: 'object',
  properties: {
    qid: { type: 'string' }, std_errors: { type: 'integer', description: '-1 if the work could not be finished' },
    recall_hops: { type: 'array', items: { type: 'string' } }, to_judge: { type: 'integer' },
    chunks: { type: 'array', items: { type: 'string' } }, note: { type: 'string' },
  },
  required: ['qid', 'std_errors', 'recall_hops', 'to_judge', 'chunks', 'note'],
}
const JUDGE = {
  type: 'object',
  properties: {
    qid: { type: 'string' }, chunk: { type: 'string' }, kept: { type: 'integer' }, dropped: { type: 'integer' },
    check_errors: { type: 'integer', description: '-1 if the work could not be finished' }, note: { type: 'string' },
  },
  required: ['qid', 'chunk', 'kept', 'dropped', 'check_errors', 'note'],
}
const ADJ = {
  type: 'object',
  properties: {
    qid: { type: 'string' }, part: { type: 'string' }, decided: { type: 'integer' }, sided_a: { type: 'integer' },
    sided_b: { type: 'integer' }, check_errors: { type: 'integer', description: '-1 if the work could not be finished' },
    pattern: { type: 'string', description: 'any pattern in the losing judge\'s errors' },
  },
  required: ['qid', 'part', 'decided', 'sided_a', 'sided_b', 'check_errors', 'pattern'],
}
const ROUTE = {
  type: 'object',
  properties: {
    qid: { type: 'string' }, output: { type: 'string', description: 'the last line the command printed, verbatim' },
    ok: { type: 'boolean', description: 'true only if the command ran and exited 0' },
  },
  required: ['qid', 'output', 'ok'],
}

const guide = `Read ${GUIDE} first and follow it exactly. Every tool command is run as: ${T} <command> (Bash timeout 600000).`

const prepPrompt = (q) => `Prepare question ${q} of set ${SET} for qrels judging. ${guide}

1. Run: ${T} show ${q}
2. If ${QD}/standards/${q}.json exists and ${T} std-check ${q} prints 0 errors, keep it. Otherwise write the hop standards (QRELS.md, step 1) to ${QD}/standards/${q}.json, one entry per live factual hop, BEFORE you open any candidate document. Make them concrete enough that a smaller model applying them makes the calls you would: the subject and the names documents use for it, what counts as full and as partial support, and the near misses to reject (another member of the same set, whose facts belong to its own hop; a namesake; a page naming the subject without the fact). Run ${T} std-check ${q} until it prints 0 errors.
3. For every hop that show marks "(needs recall)": if ${QD}/recall/${q}.candidates.json already lists it, skip this step. Otherwise write the query families (QRELS.md, step 2) to ${QD}/recall/${q}.queries.json and run ${T} recall ${q}. Recall waits for other questions' searches (it prints "waiting for the recall queue") and can take minutes: if the command is moved to the background, wait with ONE blocking command, until [ -f ${QD}/recall/${q}.candidates.json ]; do sleep 10; done (Bash timeout 600000), repeated only if it times out, and make no other calls while you wait. Give each hop queries and anchors specific to its own subject. If a hop shows nearly every fused document filtered by its anchors, or 0 candidates, fix its anchors or queries and re-run.
4. Run: ${T} chunks ${q}   and return its chunk ids and to_judge count.
Do not judge any document. Return std_errors (0 when the standard is clean), recall_hops, to_judge, chunks, and a note on anything odd about the hops. If any tool call is refused or fails so that you cannot finish, set std_errors to -1 and say what failed in note; never report success for unfinished work. Do not kill any process.`

const judgeSteps = `For every document decide keep or drop against ALL live hops of the question, not only the hop that brought it in:
   - keep: it states, about the hop's subject, a fact the hop needs (the whole hop: "full"; one part of a compound hop: "partial"). List every live hop it supports, each with one quote copied exactly from the document (at least 15 characters).
   - drop: one reason (no_hop_fact, wrong_entity, mention_only, contradicts, out_hop_only, unusable) and a note with the deciding sentence or what is missing, and what would have made it relevant.
   Being on the topic is not enough: check that the sentence is about the hop's subject, not another member of the set, a rival or a namesake. A messy or spammy page is not "unusable" when the sentence stating the fact is coherent. Derivation hops are never cited; cite the hops whose facts the computation uses.`

const judgePrompt = (q, c) => `Judge one chunk of qrels candidates: set ${SET}, question ${q}, chunk ${c}. ${guide}

1. Run: ${T} show ${q}   (the question, its live hops, the accepted evidence quotes as examples, any dataset rule)
2. Read the hop standard: ${QD}/standards/${q}.json
3. Read every part of the chunk in full: ${WK}/${q}/chunk_${c}_a.md, then _b.md, _c.md if they exist (the first line of each part lists them). If a part is too large for one Read, read it with offset/limit until you have read all of it. Read each whole document: the supporting sentence is often far down, and a fact may be split over two sentences.
4. ${judgeSteps}
5. Write ${JD}/${q}/chunk_${c}.json as {"qid": "${q}", "chunk": "${c}", "verdicts": [...]}, one verdict per document, in the format of QRELS.md.
6. Run: ${T} check ${q} ${c}   and fix every error it reports (usually a quote not copied exactly) until it prints 0 errors.
Return the counts and a one-line note. ${RULES}`

const stemOf = (rnd) => (rnd === 1 ? 'second' : `second${rnd}`)
const secondPrompt = (q, rnd, c) => `Second judge for question ${q} of set ${SET}: judge one set of documents BLIND, as an independent reviewer of a first judge's work. ${guide} Do NOT open ${JD}/${q}/chunk_*.json, any second*_compare.json or any adjudicated file: you must not see the other verdicts.

1. Run: ${T} show ${q}; read the hop standard ${QD}/standards/${q}.json
2. Read every part in full: ${WK}/${q}/${stemOf(rnd)}_${c}_a.md, then _b.md, _c.md if they exist (with offset/limit if a part is too large for one Read).
3. ${judgeSteps}
4. Write ${JD}/${q}/${stemOf(rnd)}_${c}.json as {"qid": "${q}", "chunk": "${stemOf(rnd)}_${c}", "verdicts": [...]}.
5. Run: ${T} second-check ${q} ${c} --round ${rnd}   and fix until it prints 0 errors.
Return the counts and a one-line note. ${RULES}`

const adjPrompt = (q, rnd, part) => `Adjudicate between two independent qrels judges for question ${q} of set ${SET}: they disagree on whether each document below should be kept. ${guide} ("Step 3: judge" is the standard both applied.)

1. Run: ${T} show ${q}; read the hop standard ${QD}/standards/${q}.json
2. Read the documents in full: ${WK}/${q}/adj_${part}.md (its first lines list the documents of this part; read with offset/limit if it is too large for one Read).
3. Read the two verdicts for each document in ${WK}/${q}/adj_verdicts${rnd === 1 ? '' : '_r' + rnd}.json (fields "A" and "B"). Neither judge is privileged: decide from the document and the standard.
4. For each document of this part decide under QRELS.md: keep with every live hop it supports (one exact quote each, at least 15 characters), or drop with a reason and a note.
5. Write ${JD}/${q}/adjudicated_${part}.json as {"qid": "${q}", "chunk": "adjudicated_${part}", "verdicts": [...]}.
6. Run: ${T} adj-check ${q} ${part}   and fix until it prints 0 errors.
Return the counts (how often you sided with A and with B) and any pattern in the losing judge's errors. ${RULES}`

const helperPrompt = (q, cmd) => `Run exactly this command and nothing else: ${cmd}
(Bash timeout 600000.) Return qid "${q}", the last line it printed as output (verbatim), and ok=true only if it ran and exited 0. If the command is refused or cannot run, return ok=false with the error as output. Do not read or edit any file.`

const parse = (r) => { try { return JSON.parse(r.output.trim().split('\n').pop()) } catch (e) { return null } }
const ticks = {}
const tick = (q) => (ticks[q] = (ticks[q] || 0) + 1)    // per-question counter: labels stay stable on resume

// The state on disk decides what is done; a helper that fails to report is retried up to three times.
const state = async (q) => {
  for (let i = 0; i < 3; i++) {
    const r = await agent(helperPrompt(q, `${STATE} ${q}`), { label: `state:${q}/${tick(q)}`, phase: 'Judge', schema: ROUTE, model: MODEL_HELPER, effort: 'low' })
    const s = r && parse(r)
    if (s && s.qid === q && 'complete' in s) return s
  }
  throw new Error('state unreadable after 3 tries')
}
const route = async (q, cmd, phase) => {
  for (let i = 0; i < 3; i++) {
    const r = await agent(helperPrompt(q, `${T} ${cmd}`), { label: `${cmd.split(' ')[0]}:${q}/${tick(q)}`, phase, schema: ROUTE, model: MODEL_HELPER, effort: 'low' })
    const o = r && parse(r)
    if (o) return o
  }
  return null
}

// One second-judge round to completion: blind second judge on every set without a clean verdict file (two attempts),
// compare, then the adjudicator on every part without a clean verdict file (two attempts).
const finishRound = async (q, rnd, s) => {
  let r = s.rounds[String(rnd)]
  for (let att = 1; att <= 2 && r.missing.length; att++) {
    await parallel(r.missing.map((c) => () => agent(secondPrompt(q, rnd, c) + (att > 1 ? '\n(second attempt: the first left no clean verdict file)' : ''),
      { label: `second:${q}/r${rnd}/${c}/a${att}`, phase: 'Second judge', schema: JUDGE, model: MODEL_SECOND })))
    s = await state(q); r = s.rounds[String(rnd)]
  }
  if (r.missing.length) return { error: `round ${rnd}: second-judge sets without clean verdicts: ${r.missing.join(', ')}` }
  if (!r.compared) {
    await route(q, `compare ${q} --round ${rnd}`, 'Second judge')
    s = await state(q); r = s.rounds[String(rnd)]
    if (!r.compared) return { error: `round ${rnd}: compare failed` }
  }
  for (let att = 1; att <= 2 && (r.adj_missing || []).length; att++) {
    await parallel(r.adj_missing.map((part) => () => agent(adjPrompt(q, rnd, part) + (att > 1 ? '\n(second attempt: the first left no clean verdict file)' : ''),
      { label: `adjudicate:${q}/${part}/a${att}`, phase: 'Adjudicate', schema: ADJ, model: MODEL_ADJUDICATE })))
    s = await state(q); r = s.rounds[String(rnd)]
  }
  if ((r.adj_missing || []).length) return { error: `round ${rnd}: adjudication parts without clean verdicts: ${r.adj_missing.join(', ')}` }
  return { s }
}

const run = async (q) => {
  let s = await state(q)
  if (!s.has_standard || !s.has_candidates) {
    await agent(prepPrompt(q), { label: `prepare:${q}`, phase: 'Prepare', schema: PREP, model: MODEL_PREPARE })
    s = await state(q)
    if (!s.has_standard || !s.has_candidates) return { qid: q, error: `prepare incomplete on disk: ${s.blocking}` }
  }
  if (!s.chunked) {
    await route(q, `chunks ${q}`, 'Prepare')
    s = await state(q)
    if (!s.chunked) return { qid: q, error: 'chunks not rendered' }
  }
  for (let att = 1; att <= 2 && s.unjudged.length; att++) {
    await parallel(s.unjudged.map((c) => () => agent(judgePrompt(q, c) + (att > 1 ? '\n(second attempt: the first left no clean verdict file)' : ''),
      { label: `judge:${q}/${c}/a${att}`, phase: 'Judge', schema: JUDGE, model: att === 1 ? MODEL_JUDGE : MODEL_RETRY })))
    s = await state(q)
  }
  if (s.unjudged.length) return { qid: q, error: `first-judge chunks without clean verdicts: ${s.unjudged.join(', ')}` }
  for (let i = 0; i < MAX_ROUNDS && !s.complete; i++) {
    if (s.pending_round) {
      const f = await finishRound(q, s.pending_round, s)
      if (f.error) return { qid: q, error: f.error }
      s = f.s
    } else if (s.next_round) {
      const nr = s.next_round
      await route(q, `second ${q} --round ${nr.round}${nr.flags ? ' ' + nr.flags : ''}`, 'Second judge')
      s = await state(q)
      if (!s.rounds[String(nr.round)]) return { qid: q, error: `round ${nr.round}: render failed` }
    } else break
  }
  if (!s.complete) return { qid: q, error: `incomplete on disk: ${s.blocking}` }
  const rounds = Object.entries(s.rounds).map(([k, r]) => ({ round: Number(k), drops_only: r.drops_only, sets: r.rendered.length,
    flips: r.first_drop_second_keep || 0, disagree: r.disagree || 0, adj_parts: (r.adj_parts || []).length }))
  return { qid: q, complete: true, to_judge: s.to_judge, chunks: s.chunks.length, rounds, escalated: rounds.some((r) => r.drops_only) }
}

if (!SET || !QIDS.length) { log('Pass args {set, qids}: nothing to do.'); return { complete: 0 } }
if (RUN.startsWith('/path/to') || PIPE.startsWith('/path/to')) { log('Set PIPELINE and RUN_DIR at the top of the script (or pass {run, pipeline} in args).'); return { complete: 0 } }
log(`Stage 3 phase 1: ${QIDS.length} questions of set ${SET}; resuming from disk`)
const out = (await pipeline(QIDS, (q) => run(q).catch((e) => ({ qid: q, error: String(e) })))).filter(Boolean)
const failed = out.filter((r) => !r.complete)
log(`complete ${out.length - failed.length} of ${QIDS.length}${failed.length ? '; not complete: ' + failed.map((r) => r.qid).join(', ') : ''}`)
log(`next: ${T} build && ${T} verify`)
return out
