export const meta = {
  name: 'cmass-stage3-qrels-phase2',
  description: 'CMASS Stage 3 phase 2 (duplicate expansion): agent verification of the near duplicates (5-gram Jaccard >= 0.7) of kept documents that the quote check left for agents, one round of the expansion. The hop standards exist from phase 1; dups.py chunks has settled by quote every near duplicate holding its parent\'s quote for a hop verbatim (near_dup_mode quote_first, the default) and rendered the rest; a cheaper model verifies each of those on its own text, a stronger blind second judge re-judges the policy sample, an adjudicator decides each disagreement. Exact and quote-settled duplicates need no agent (dups.py apply adds them). Resumes from disk (qrels_state.py --phase2 N).',
  whenToUse: 'After dups.py lookup, fetch and chunks for round N; args {set, round, qids} as dups.py chunks prints them.',
  phases: [
    { title: 'Verify', detail: 'one agent per chunk of near duplicates; a chunk whose verdicts fail the check is re-judged by the stronger model' },
    { title: 'Second judge', detail: 'blind, one agent per sample set; rounds and escalation come from qrels_state.py' },
    { title: 'Adjudicate', detail: 'one agent per disagreement part, the two verdicts shown as A and B' },
  ],
}

// ------------------------------------------------------------------ paths (point them at your checkout and run)
const PIPELINE = '/path/to/cmass/pipelines/cmass'   // this repository's pipelines/cmass directory
const RUN_DIR = '/path/to/run'                      // the run directory (cmass.json, final/, qrels/)
// ------------------------------------------------------------------ models
const MODEL_JUDGE = 'haiku'        // first verification of every near-duplicate chunk (the cheaper model)
const MODEL_RETRY = 'sonnet'       // second attempt at a chunk whose verdicts failed the check
const MODEL_SECOND = 'sonnet'      // blind second judge
const MODEL_ADJUDICATE = 'sonnet'  // decides each disagreement
const MODEL_HELPER = 'haiku'       // runs one deterministic command and reports what it printed
const MAX_ROUNDS = 6

// args: {set: "<set>", round: N, qids: ["<qid>", ...], run?: RUN_DIR, pipeline?: PIPELINE}; `dups.py chunks --round N`
// prints them as workflow_args. The tiering parameters come from the run config ("phase2_tiers", else "tiers").
const A = typeof args === 'string' ? JSON.parse(args) : (args || {})
const SET = A.set
const ROUND = Number(A.round || 1)
const QIDS = A.qids || []
const PIPE = A.pipeline || PIPELINE
const RUN = A.run || RUN_DIR
const T = `cd ${RUN} && python3 ${PIPE}/stage3/qrels.py --set ${SET} --phase2 ${ROUND}`
const STATE = `cd ${RUN} && python3 ${PIPE}/stage3/qrels_state.py --set ${SET} --phase2 ${ROUND}`
const GUIDE = `${PIPE}/stage3/QRELS.md`
const QD = `${RUN}/qrels/${SET}`
const WK = `${QD}/phase2/r${ROUND}/work`
const JD = `${QD}/phase2/r${ROUND}/judged`
const RULES = 'If any tool call is refused or fails so that you cannot finish, stop and report failure: set check_errors to -1 (never 0 for unfinished work) and say in note what failed. Never report success for work you did not finish. Do not kill any process.'

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

const guide = `Read ${GUIDE} first and follow it exactly, including "Phase 2: near duplicates". Every tool command is run as: ${T} <command> (Bash timeout 600000).`
const nearNote = `These documents are near duplicates (5-gram word-shingle Jaccard >= 0.7) of documents already kept for this question. Each document's header names its parent, the hops the parent is kept for, the parent's quotes and whether each quote is verbatim in this copy: use it to check whether the copy gives the same supporting evidence, but judge the copy on ITS OWN text, against ALL live hops. A copy can lack the very sentence its parent was kept for (drop it then), or support a hop its parent did not. Every keep needs a quote copied from this copy.`

const judgeSteps = `For every document decide keep or drop against ALL live hops of the question:
   - keep: its own text states, about the hop's subject, a fact the hop needs ("full", or "partial" for one part of a compound hop). List every live hop it supports, each with one quote copied exactly from this document (at least 15 characters).
   - drop: one reason (no_hop_fact, wrong_entity, mention_only, contradicts, out_hop_only, unusable) and a note with what is missing (for a copy that lost its parent's sentence, say so) and what would have made it relevant.
   Derivation hops are never cited.`

const judgePrompt = (q, c) => `Verify one chunk of near duplicates: set ${SET}, question ${q}, expansion round ${ROUND}, chunk ${c}. ${guide}
${nearNote}

1. Run: ${T} show ${q}
2. Read the hop standard: ${QD}/standards/${q}.json
3. Read every part of the chunk in full: ${WK}/${q}/chunk_${c}_a.md, then _b.md, _c.md if they exist (with offset/limit if a part is too large for one Read).
4. ${judgeSteps}
5. Write ${JD}/${q}/chunk_${c}.json as {"qid": "${q}", "chunk": "${c}", "verdicts": [...]}, one verdict per document.
6. Run: ${T} check ${q} ${c}   and fix every error it reports until it prints 0 errors.
Return the counts and a one-line note. ${RULES}`

const stemOf = (rnd) => (rnd === 1 ? 'second' : `second${rnd}`)
const secondPrompt = (q, rnd, c) => `Second judge for the near duplicates of question ${q} (set ${SET}, expansion round ${ROUND}): judge BLIND, as an independent reviewer. ${guide} Do NOT open ${JD}/${q}/chunk_*.json, any second*_compare.json or any adjudicated file.
${nearNote}

1. Run: ${T} show ${q}; read the hop standard ${QD}/standards/${q}.json
2. Read every part in full: ${WK}/${q}/${stemOf(rnd)}_${c}_a.md, then _b.md, _c.md if they exist.
3. ${judgeSteps}
4. Write ${JD}/${q}/${stemOf(rnd)}_${c}.json as {"qid": "${q}", "chunk": "${stemOf(rnd)}_${c}", "verdicts": [...]}.
5. Run: ${T} second-check ${q} ${c} --round ${rnd}   and fix until it prints 0 errors.
Return the counts and a one-line note. ${RULES}`

const adjPrompt = (q, rnd, part) => `Adjudicate between two independent judges of near duplicates for question ${q} (set ${SET}, expansion round ${ROUND}): they disagree on whether each document below should be kept. ${guide}
${nearNote}

1. Run: ${T} show ${q}; read the hop standard ${QD}/standards/${q}.json
2. Read the documents in full: ${WK}/${q}/adj_${part}.md
3. Read the two verdicts for each document in ${WK}/${q}/adj_verdicts${rnd === 1 ? '' : '_r' + rnd}.json (fields "A" and "B"). Neither judge is privileged: decide from the document and the standard.
4. For each document of this part: keep with every live hop it supports (one exact quote each, at least 15 characters), or drop with a reason and a note.
5. Write ${JD}/${q}/adjudicated_${part}.json as {"qid": "${q}", "chunk": "adjudicated_${part}", "verdicts": [...]}.
6. Run: ${T} adj-check ${q} ${part}   and fix until it prints 0 errors.
Return the counts (how often you sided with A and with B) and any pattern in the losing judge's errors. ${RULES}`

const helperPrompt = (q, cmd) => `Run exactly this command and nothing else: ${cmd}
(Bash timeout 600000.) Return qid "${q}", the last line it printed as output (verbatim), and ok=true only if it ran and exited 0. If the command is refused or cannot run, return ok=false with the error as output. Do not read or edit any file.`

const parse = (r) => { try { return JSON.parse(r.output.trim().split('\n').pop()) } catch (e) { return null } }
const ticks = {}
const tick = (q) => (ticks[q] = (ticks[q] || 0) + 1)

const state = async (q) => {
  for (let i = 0; i < 3; i++) {
    const r = await agent(helperPrompt(q, `${STATE} ${q}`), { label: `state:${q}/${tick(q)}`, phase: 'Verify', schema: ROUTE, model: MODEL_HELPER, effort: 'low' })
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
  if (s.complete && !s.to_judge) return { qid: q, complete: true, near_duplicates: 0, chunks: 0, rounds: [] }  // settled by quote
  if (!s.has_standard) return { qid: q, error: 'no clean hop standard: phase 1 writes standards/<qid>.json' }
  if (!s.chunked) {
    await route(q, `chunks ${q}`, 'Verify')
    s = await state(q)
    if (!s.chunked) return { qid: q, error: 'chunks not rendered (run dups.py fetch and chunks for this round)' }
  }
  for (let att = 1; att <= 2 && s.unjudged.length; att++) {
    await parallel(s.unjudged.map((c) => () => agent(judgePrompt(q, c) + (att > 1 ? '\n(second attempt: the first left no clean verdict file)' : ''),
      { label: `verify:${q}/${c}/a${att}`, phase: 'Verify', schema: JUDGE, model: att === 1 ? MODEL_JUDGE : MODEL_RETRY })))
    s = await state(q)
  }
  if (s.unjudged.length) return { qid: q, error: `chunks without clean verdicts: ${s.unjudged.join(', ')}` }
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
  return { qid: q, complete: true, near_duplicates: s.to_judge, chunks: s.chunks.length, rounds }
}

if (!SET || !QIDS.length) { log('Pass args {set, round, qids} (dups.py chunks prints them): nothing to do.'); return { complete: 0 } }
if (RUN.startsWith('/path/to') || PIPE.startsWith('/path/to')) { log('Set PIPELINE and RUN_DIR at the top of the script (or pass {run, pipeline} in args).'); return { complete: 0 } }
log(`Stage 3 phase 2, expansion round ${ROUND}: near duplicates of ${QIDS.length} questions of set ${SET}; resuming from disk`)
const out = (await pipeline(QIDS, (q) => run(q).catch((e) => ({ qid: q, error: String(e) })))).filter(Boolean)
const failed = out.filter((r) => !r.complete)
log(`complete ${out.length - failed.length} of ${QIDS.length}${failed.length ? '; not complete: ' + failed.map((r) => r.qid).join(', ') : ''}`)
log(`next: cd ${RUN} && python3 ${PIPE}/stage3/dups.py --set ${SET} apply --round ${ROUND} && python3 ${PIPE}/stage3/dups.py --set ${SET} lookup --round ${ROUND + 1}`)
return out
