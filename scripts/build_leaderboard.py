#!/usr/bin/env python3
"""Write the leaderboard rows of docs/leaderboard/index.html from the published evaluation results."""
from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))
from prepare_inputs import COUNTS, QREL_COUNTS

PAGE = ROOT / "docs/leaderboard/index.html"
SETS = [("bcp", "BrowseComp-Plus (BCP)"), ("trqa", "Total Recall QA (TRQA)"),
        ("dsqa", "DeepSearchQA (DSQA)"), ("hotpotqa", "HotpotQA")]  # names as in the paper
MODELS = {
    "sol": ("GPT-6.1 Sol", "https://developers.openai.com/api/docs/models/gpt-6.1-sol", "OpenAI", "API", "high reasoning effort"),
    "gemma": ("Gemma 4 31B", "https://huggingface.co/google/gemma-4-31B-it", "Google DeepMind", "open weights", "IT, thinking enabled"),
    "qwen": ("Qwen 3.5 9B", "https://huggingface.co/Qwen/Qwen3.5-9B", "Qwen", "open weights", "thinking enabled"),
}
HARNESS = ("Piika", "https://github.com/castorini/piika")  # every reported run
COLS = ["acc", "p_all", "r_all", "hg", "tc", "cb"]
BOLD = {"acc", "p_all", "r_all", "hg", "cb"}  # higher is better; tool calls are not ranked


def one_decimal(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.1"), ROUND_HALF_UP)  # as in the paper's tables


def table() -> dict:
    results = json.loads((ROOT / "evaluation/results.json").read_text())
    hops = json.loads((ROOT / "evaluation/hop_groundedness_results.json").read_text())["results"]
    accuracy = {(r["dataset"], r["model"], r["condition"]): r.get("accuracy_percent", r.get("strict_accuracy_percent"))
                for r in results["accuracy"]}
    coverage = {(r["dataset"], r["model"]): r["hop_coverage_percent"] for r in hops}
    rows = {}
    for r in results["retrieval"]:
        ds, model = r["dataset"], r["model"]
        rows.setdefault(ds, []).append(dict(
            model=model, acc=accuracy[ds, model, "piika"], cb=accuracy[ds, model, "closedbook"],
            p_all=r["precision_at_all_percent"], r_all=r["recall_at_all_percent"], tc=r["mean_tool_calls"],
            hg=coverage[ds, "gpt" if model == "sol" else model]))
    return {ds: [{k: v if k == "model" else one_decimal(v) for k, v in r.items()} for r in rows[ds]] for ds in rows}


def rows() -> list[str]:
    data, out = table(), []
    h_name, h_url = HARNESS
    for key, label in SETS:
        ranked = sorted(data[key], key=lambda r: -r["acc"])
        best = {c: max(r[c] for r in ranked) for c in BOLD}
        out.append(f'          <tbody data-set="{key}">')
        out.append(f'            <tr class="set-row"><th scope="rowgroup" colspan="9">{label}'
                   f'<span>{COUNTS[key]} questions &middot; {QREL_COUNTS[key]:,} qrels</span></th></tr>')
        for rank, r in enumerate(ranked, 1):
            name, url, org, kind, note = MODELS[r["model"]]
            cells = []
            for c in COLS:
                cls = ["num"] + (["key"] if c == "acc" else []) + (["best"] if c in BOLD and r[c] == best[c] else [])
                cells.append(f'<td class="{" ".join(cls)}">{r[c]}</td>')
            model = (f'<td class="model"><a href="{url}">{name.replace(" ", "&nbsp;")}</a>'
                     f'<span class="cell-sub"><span class="tag">{kind}</span> {org} &middot; {note}</span>'
                     f'<span class="cell-sub harness-sub">Harness: <a href="{h_url}">{h_name}</a></span></td>')
            harness = f'<td class="harness"><a href="{h_url}">{h_name}</a></td>'
            out.append(f'            <tr><td class="rank">{rank}</td>{model}{harness}{"".join(cells)}</tr>')
        out.append('          </tbody>')
    return out


if __name__ == "__main__":
    html = PAGE.read_text()
    start, end = "<!-- rows:start -->", "<!-- rows:end -->"
    i, j = html.index(start) + len(start), html.index(end)
    PAGE.write_text(html[:i] + "\n" + "\n".join(rows()) + "\n          " + html[j:])
