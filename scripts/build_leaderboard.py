#!/usr/bin/env python3
"""Build the leaderboard from pinned, aggregate-only evaluation results."""
from __future__ import annotations

import html
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))
from prepare_inputs import COUNTS, QREL_COUNTS

PAGE = ROOT / "docs/leaderboard/index.html"
SYSTEM_PAGE = ROOT / "docs/leaderboard/systems/index.html"
SETS = [("bcp", "BrowseComp-Plus (BCP)"), ("trqa", "Total Recall QA (TRQA)"),
        ("dsqa", "DeepSearchQA (DSQA)"), ("hotpotqa", "HotpotQA")]
MODELS = {
    "sol": ("GPT-6.1 Sol", "https://developers.openai.com/api/docs/models/gpt-6.1-sol", "OpenAI", "API", "high reasoning effort"),
    "gemma": ("Gemma 4 31B", "https://huggingface.co/google/gemma-4-31B-it", "Google DeepMind", "open weights", "IT, thinking enabled"),
    "qwen": ("Qwen 3.5 9B", "https://huggingface.co/Qwen/Qwen3.5-9B", "Qwen", "open weights", "thinking enabled"),
}
COLS = ["acc", "p_all", "r_all", "hg", "tc", "cb"]
BOLD = {"acc", "p_all", "r_all", "hg", "cb"}


def one_decimal(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.1"), ROUND_HALF_UP)


def jevgpt_results() -> dict:
    return json.loads((ROOT / "evaluation/jevgpt_results.json").read_text())


def table() -> dict:
    results = json.loads((ROOT / "evaluation/results.json").read_text())
    hops = json.loads((ROOT / "evaluation/hop_groundedness_results.json").read_text())["results"]
    accuracy = {(r["dataset"], r["model"], r["condition"]): r for r in results["accuracy"]}
    coverage = {(r["dataset"], r["model"]): r["hop_coverage_percent"] for r in hops}
    data = {}
    for r in results["retrieval"]:
        ds, model = r["dataset"], r["model"]
        a, cb = accuracy[ds, model, "piika"], accuracy[ds, model, "closedbook"]
        data.setdefault(ds, []).append(dict(
            system="piika-" + model, model=model, harness="Piika",
            harness_url="https://github.com/castorini/piika", retriever="BM25",
            retriever_note="k₁ = 0.9, b = 0.4", evidence="None", evidence_note="",
            acc=a.get("accuracy_percent", a.get("strict_accuracy_percent")),
            relaxed=a.get("relaxed_accuracy_percent"),
            cb=cb.get("accuracy_percent", cb.get("strict_accuracy_percent")),
            p_all=r["precision_at_all_percent"], r_all=r["recall_at_all_percent"],
            tc=r["mean_tool_calls"], hg=coverage[ds, "gpt" if model == "sol" else model]))
    jev = jevgpt_results()
    if jev["dataset_release"] != results["qrels_release"] or jev["versions"]["dataset-source"] != results["dataset_revision"]:
        raise ValueError("Leaderboard systems must use the same pinned qrels")
    seen = set()
    for r in jev["results"]:
        ds, system = r["dataset"], r["system"]
        if (ds, system) in seen or r["questions"] != COUNTS[ds]:
            raise ValueError("Duplicate or incomplete JevGPT cohort")
        seen.add((ds, system))
        setup = jev["systems"][system]
        if r["independent_accuracy_percent"] is None:
            raise ValueError("Independent accuracy is required for ranking")
        data[ds].append(dict(
            system=system, model="sol", harness="JevGPT", harness_url=jev["code_url"],
            retriever=setup["retriever"], retriever_note=setup["retriever_note"],
            evidence=setup["evidence_model"], evidence_note="System One evidence", paper=jev["paper_url"],
            acc=r["independent_accuracy_percent"], relaxed=r["independent_relaxed_accuracy_percent"] if ds == "dsqa" else None,
            cb=None, tc=r["mean_tool_calls"], p_all=r["precision_at_all_percent"], r_all=r["recall_at_all_percent"],
            hg=r["hop_groundedness_percent"]))
    if seen != {(ds, system) for ds, _ in SETS for system in jev["systems"]}:
        raise ValueError("Expected every released dataset for each included JevGPT system")
    return data


def link(url: str, label: str, *, new_tab: bool = False) -> str:
    attrs = ' target="_blank" rel="noopener" title="Opens in a new tab"' if new_tab else ""
    return f'<a href="{html.escape(url, quote=True)}"{attrs}>{html.escape(label)}</a>'


def rows() -> list[str]:
    data, out = table(), []
    for key, label in SETS:
        ranked = sorted(data[key], key=lambda r: -r["acc"])
        best = {c: max(r[c] for r in ranked if r[c] is not None) for c in BOLD}
        out.append(f'          <tbody data-set="{key}">')
        out.append(f'            <tr class="set-row"><th scope="rowgroup" colspan="8">{label}'
                   f'<span>{COUNTS[key]} questions &middot; {QREL_COUNTS[key]:,} qrels</span></th></tr>')
        previous, rank = None, 0
        for position, r in enumerate(ranked, 1):
            if r["acc"] != previous:
                rank = position
            previous = r["acc"]
            name, url, org, kind, note = MODELS[r["model"]]
            cells = []
            for c in COLS:
                cls = ["num"] + (["key"] if c == "acc" else []) + (["best"] if c in BOLD and r[c] == best[c] else [])
                if r[c] is None:
                    value = '<span aria-label="Unavailable" title="Not evaluated for this system">—</span>'
                else:
                    value = str(one_decimal(r[c]))
                if c == "tc" and r["harness"] == "JevGPT":
                    value = f'<a class="tool-detail" href="systems/#jev-tool-usage" target="_blank" rel="noopener" title="Mean total tool calls per question; breakdown opens in a new tab">{value}</a>'
                if c == "acc" and r["relaxed"] is not None:
                    value += f'<span class="cell-sub relaxed">{one_decimal(r["relaxed"])} relaxed</span>'
                cells.append(f'<td class="{" ".join(cls)}">{value}</td>')
            is_jev = r["harness"] == "JevGPT"
            info_url = "systems/#system-" + r["system"].lower()
            title = link(info_url, r["system"] if is_jev else name, new_tab=True)
            detail = link(info_url, "System info ↗", new_tab=True)
            model = f'<td class="model">{title}<span class="cell-sub">{detail}</span></td>'
            out.append(f'            <tr data-system="{r["system"]}"><td class="rank">{rank}</td>{model}{"".join(cells)}</tr>')
        out.append('          </tbody>')
    return out


def tool_rows() -> list[str]:
    data = jevgpt_results()["results"]
    out = []
    for ds, _ in SETS:
        out.append(f'          <tbody data-set="{ds}">')
        for r in data:
            if r["dataset"] != ds:
                continue
            tools = r["tool_calls"]
            values = [ds.upper() if ds != "hotpotqa" else "HotpotQA", r["system"]]
            values += [str(tools.get(t, 0)) for t in ("search", "read_search_results", "jev_find", "read_document")]
            values += [str(one_decimal(r["mean_tool_calls"]))]
            out.append('            <tr>' + ''.join(f'<td>{html.escape(v)}</td>' for v in values) + '</tr>')
        out.append('          </tbody>')
    return out


def piika_models() -> list[str]:
    out = []
    for model, (name, url, org, kind, note) in MODELS.items():
        out.append(f'      <article class="system-model" id="system-piika-{model}" tabindex="-1">')
        out.append(f'        <h3>{link(url, name)}</h3>')
        out.append(f'        <p>{org} &middot; {kind} &middot; {note}</p>')
        out.append('      </article>')
    return out


def main() -> None:
    for page, sections in [(PAGE, [("rows", rows())]),
                           (SYSTEM_PAGE, [("jev-tools", tool_rows()), ("piika-models", piika_models())])]:
        text = page.read_text()
        for name, content in sections:
            start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
            i, j = text.index(start) + len(start), text.index(end)
            text = text[:i] + "\n" + "\n".join(content) + "\n          " + text[j:]
        page.write_text(text)


if __name__ == "__main__":
    main()
