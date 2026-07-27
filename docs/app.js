const state = {
  payload: null,
  questions: [],
  filtered: [],
  selectedId: null,
  hopFilter: "all",
  canvasObserver: null,
};

const listElement = document.querySelector("#question-list");
const detailElement = document.querySelector("#question-detail");
const searchElement = document.querySelector("#question-search");
const matchCountElement = document.querySelector("#match-count");

function formatNumber(value) {
  return new Intl.NumberFormat("en-US").format(value);
}

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) element.textContent = value;
}

function searchableText(question) {
  const hopText = question.hops.flatMap((hop) => [
    hop.clue,
    ...hop.qrel,
    ...hop.excerpts.flatMap((excerpt) => [excerpt.docid, excerpt.snippet]),
  ]);
  return [question.record_id, question.question, question.answer, ...hopText].join(" ").toLowerCase();
}

function currentQuestion() {
  return state.questions.find((question) => question.record_id === state.selectedId) || null;
}

function selectQuestion(recordId, options = {}) {
  if (!state.questions.some((question) => question.record_id === recordId)) return;
  state.selectedId = recordId;
  if (!options.preserveFilter) state.hopFilter = "all";
  history.replaceState(null, "", `#qid-${recordId}`);
  renderList();
  renderDetail();
  if (options.focus) {
    detailElement.focus({ preventScroll: true });
    if (window.matchMedia("(max-width: 760px)").matches) {
      detailElement.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }
}

function questionRow(question) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "question-row";
  button.setAttribute("aria-current", question.record_id === state.selectedId ? "true" : "false");
  button.addEventListener("click", () => selectQuestion(question.record_id, { focus: true }));

  const id = document.createElement("span");
  id.className = "question-id";
  id.textContent = `Q${question.record_id}`;

  const copy = document.createElement("span");
  copy.className = "question-row-copy";
  const title = document.createElement("strong");
  title.textContent = question.question;
  const meta = document.createElement("span");
  meta.className = "question-row-meta";
  const required = question.hops.filter((hop) => !hop.redundant).length;
  meta.innerHTML = `<span>${question.hops.length} hops</span><span>${required} required</span><span>${formatNumber(question.qrel.length)} docs</span>`;
  copy.append(title, meta);
  button.append(id, copy);
  item.append(button);
  return item;
}

function renderList() {
  const fragment = document.createDocumentFragment();
  state.filtered.forEach((question) => fragment.append(questionRow(question)));
  listElement.replaceChildren(fragment);
  matchCountElement.textContent = formatNumber(state.filtered.length);
  if (!state.filtered.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "No matching questions";
    listElement.append(item);
  }
}

function navButton(label, symbol, targetId, disabled) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-button";
  button.textContent = symbol;
  button.title = label;
  button.setAttribute("aria-label", label);
  button.disabled = disabled;
  if (!disabled) button.addEventListener("click", () => selectQuestion(targetId, { focus: true }));
  return button;
}

function excerptCard(excerpt) {
  const card = document.createElement("article");
  card.className = "excerpt-card";
  const meta = document.createElement("div");
  meta.className = "excerpt-meta";
  const docid = document.createElement("span");
  docid.className = "docid";
  docid.textContent = excerpt.docid;
  const support = document.createElement("span");
  support.className = `support-label ${excerpt.support.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  support.textContent = excerpt.support;
  meta.append(docid, support);

  const source = document.createElement("p");
  source.className = "excerpt-source";
  source.textContent = excerpt.source;
  const quote = document.createElement("blockquote");
  quote.textContent = excerpt.snippet;
  card.append(meta, source, quote);
  if (excerpt.note) {
    const note = document.createElement("p");
    note.className = "verifier-note";
    note.textContent = excerpt.note;
    card.append(note);
  }
  return card;
}

function qrelDetails(hop) {
  const details = document.createElement("details");
  details.className = "qrel-details";
  const summary = document.createElement("summary");
  summary.textContent = `All ${formatNumber(hop.qrel.length)} supporting documents`;
  const panel = document.createElement("div");
  panel.className = "qrel-panel";
  const code = document.createElement("pre");
  code.textContent = hop.qrel.join("\n");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-button";
  button.textContent = "Copy IDs";
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(hop.qrel.join("\n"));
      button.textContent = "Copied";
      window.setTimeout(() => { button.textContent = "Copy IDs"; }, 1400);
    } catch {
      button.textContent = "Copy unavailable";
    }
  });
  panel.append(code, button);
  details.append(summary, panel);
  return details;
}

function hopSection(hop) {
  const section = document.createElement("section");
  section.className = `hop-section${hop.redundant ? " redundant" : ""}`;
  const heading = document.createElement("div");
  heading.className = "hop-heading";
  const number = document.createElement("span");
  number.className = "hop-number";
  number.textContent = `H${hop.hop_id}`;
  const title = document.createElement("h3");
  title.textContent = hop.clue;
  const type = document.createElement("span");
  type.className = "hop-type";
  type.textContent = hop.redundant ? "Confirmatory" : "Required";
  heading.append(number, title, type);

  const excerpts = document.createElement("div");
  excerpts.className = "excerpt-grid";
  hop.excerpts.forEach((excerpt) => excerpts.append(excerptCard(excerpt)));
  section.append(heading, excerpts, qrelDetails(hop));
  return section;
}

function segmentButton(label, value) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.setAttribute("aria-pressed", state.hopFilter === value ? "true" : "false");
  button.addEventListener("click", () => {
    state.hopFilter = value;
    renderDetail();
  });
  return button;
}

function drawCoverage(canvas, question) {
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  canvas.width = Math.round(rect.width * ratio);
  canvas.height = Math.round(rect.height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  const left = 34;
  const right = Math.max(left, width - 34);
  const centerY = 48;
  const count = question.hops.length;
  const step = count > 1 ? (right - left) / (count - 1) : 0;

  context.clearRect(0, 0, width, height);
  context.strokeStyle = "#cad4ce";
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(left, centerY);
  context.lineTo(right, centerY);
  context.stroke();

  question.hops.forEach((hop, index) => {
    const x = count > 1 ? left + step * index : width / 2;
    const color = hop.redundant ? "#8a6719" : "#176b52";
    const barHeight = Math.min(48, 8 + Math.log2(hop.qrel.length + 1) * 7);
    context.fillStyle = hop.redundant ? "#f4eccf" : "#dcefe7";
    context.fillRect(x - 7, centerY + 18, 14, barHeight);
    context.strokeStyle = color;
    context.strokeRect(x - 7, centerY + 18, 14, barHeight);
    context.beginPath();
    context.fillStyle = color;
    context.arc(x, centerY, 8, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#17221d";
    context.font = "700 10px ui-monospace, SFMono-Regular, Menlo, monospace";
    context.textAlign = "center";
    context.fillText(`H${hop.hop_id}`, x, 22);
    context.fillStyle = "#5d6963";
    context.font = "10px Inter, system-ui, sans-serif";
    context.fillText(formatNumber(hop.qrel.length), x, Math.min(height - 4, centerY + 30 + barHeight));
  });
}

function installCoverageObserver(canvas, question) {
  if (state.canvasObserver) state.canvasObserver.disconnect();
  const render = () => drawCoverage(canvas, question);
  state.canvasObserver = new ResizeObserver(render);
  state.canvasObserver.observe(canvas);
  render();
}

function renderDetail() {
  const question = currentQuestion();
  if (!question) {
    detailElement.innerHTML = '<div class="empty-state">Select a question</div>';
    return;
  }
  const selectedIndex = state.questions.findIndex((item) => item.record_id === question.record_id);
  const previous = state.questions[selectedIndex - 1];
  const next = state.questions[selectedIndex + 1];

  const kicker = document.createElement("div");
  kicker.className = "detail-kicker";
  const id = document.createElement("p");
  id.textContent = `QUESTION ${question.record_id}`;
  const navigation = document.createElement("div");
  navigation.className = "detail-nav";
  navigation.append(
    navButton("Previous question", "\u2190", previous?.record_id, !previous),
    navButton("Next question", "\u2192", next?.record_id, !next),
  );
  kicker.append(id, navigation);

  const title = document.createElement("h2");
  title.className = "detail-question";
  title.textContent = question.question;
  const answer = document.createElement("p");
  answer.className = "answer-line";
  const answerLabel = document.createElement("span");
  answerLabel.textContent = "Answer";
  const answerText = document.createElement("strong");
  answerText.textContent = question.answer;
  answer.append(answerLabel, answerText);

  const figure = document.createElement("figure");
  figure.className = "coverage-figure";
  const caption = document.createElement("figcaption");
  caption.innerHTML = '<strong>Hop coverage</strong><span class="legend"><span>Required</span><span>Confirmatory</span></span>';
  const canvas = document.createElement("canvas");
  canvas.id = "coverage-canvas";
  canvas.setAttribute("aria-label", `${question.hops.length} grounded hops; bar height represents qrel size`);
  canvas.setAttribute("role", "img");
  figure.append(caption, canvas);

  const toolbar = document.createElement("div");
  toolbar.className = "hop-toolbar";
  const hopTitle = document.createElement("h2");
  hopTitle.textContent = `${question.hops.length} grounded hops / ${formatNumber(question.qrel.length)} documents`;
  const controls = document.createElement("div");
  controls.className = "segmented-control";
  controls.setAttribute("aria-label", "Hop filter");
  controls.append(segmentButton("All hops", "all"), segmentButton("Required", "required"), segmentButton("Confirmatory", "redundant"));
  toolbar.append(hopTitle, controls);

  const hops = document.createElement("div");
  hops.className = "hops";
  question.hops
    .filter((hop) => state.hopFilter === "all" || (state.hopFilter === "redundant") === hop.redundant)
    .forEach((hop) => hops.append(hopSection(hop)));

  detailElement.replaceChildren(kicker, title, answer, figure, toolbar, hops);
  installCoverageObserver(canvas, question);
}

function applySearch() {
  const terms = searchElement.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  state.filtered = terms.length
    ? state.questions.filter((question) => terms.every((term) => question._search.includes(term)))
    : [...state.questions];
  if (!state.filtered.length) {
    state.selectedId = null;
  } else if (!state.filtered.some((question) => question.record_id === state.selectedId)) {
    state.selectedId = state.filtered[0].record_id;
  }
  renderList();
  renderDetail();
}

async function initialize() {
  try {
    const response = await fetch("data/bcp.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    state.questions = state.payload.questions.map((question) => ({ ...question, _search: searchableText(question) }));
    state.filtered = [...state.questions];
    const hashMatch = window.location.hash.match(/^#qid-(.+)$/);
    state.selectedId = hashMatch && state.questions.some((question) => question.record_id === hashMatch[1])
      ? hashMatch[1]
      : state.questions[0].record_id;

    setText("#stat-questions", formatNumber(state.payload.stats.questions));
    setText("#stat-hops", formatNumber(state.payload.stats.hops));
    setText("#stat-qrels", formatNumber(state.payload.stats.question_qrel_pairs));
    setText("#stat-excerpts", formatNumber(state.payload.stats.evidence_excerpts));
    searchElement.addEventListener("input", applySearch);
    window.addEventListener("hashchange", () => {
      const match = window.location.hash.match(/^#qid-(.+)$/);
      if (match) selectQuestion(match[1], { preserveFilter: true });
    });
    renderList();
    renderDetail();
  } catch (error) {
    const message = document.createElement("div");
    message.className = "error-state";
    message.textContent = `Unable to load release data: ${error.message}`;
    detailElement.replaceChildren(message);
  }
}

initialize();
