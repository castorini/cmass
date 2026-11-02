#!/usr/bin/env python3
"""Throughput of stage3/scan.py on real corpus shards, with invented rules. Prints timings and aggregate counts only,
never document text or document ids; everything it writes goes under --work (keep that outside the repository).

  python3 pipelines/cmass/demo/scan_bench.py --corpus /data/climbmix --shards 0:1 --work /scratch/scan_bench \
      [--rules 300] [--matchers aho,token] [--parallel 32] [--total-docs 563e6] [--workers 32]

For each matcher: one single-worker scan of the selected shards (docs/s per worker), a merge, and a check that every
matcher found exactly the same matches. --parallel K then runs K single-worker scans of the same shards at once (the
first matcher; warm page cache, so it measures CPU and memory contention, not disk), for a per-worker rate under load.
The projection is --total-docs / (--workers x rate).

The rules are generated from generic topic and context words (fixed seed): a few hundred rules shaped like hop rules,
two or three "all" groups of one to five alternatives, some with "none" phrases, mixing rare and common words.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

SCAN = Path(__file__).resolve().parent.parent / "stage3" / "scan.py"

TOPICS = """coral reef|bycatch|IUU|illegal fishing|trawler|aquifer|groundwater|desalination|photovoltaic|solar panel|
wind turbine|offshore wind|geothermal|hydropower|tidal energy|lithium|cobalt|rare earth|electric vehicle|
charging station|hydrogen|fuel cell|carbon tax|emissions trading|cap and trade|carbon capture|deforestation|
reforestation|mangrove|peatland|wetland|permafrost|glacier|sea level|storm surge|monsoon|drought|wildfire|heatwave|
malaria|tuberculosis|measles|cholera|dengue|vaccine|antibiotic|insulin|dialysis|telemedicine|opioid|minimum wage|
trade union|collective bargaining|pension|tax credit|property tax|tariff|sanctions|embargo|inflation|interest rate|
central bank|bond yield|cryptocurrency|blockchain|bitcoin|stock exchange|venture capital|semiconductor|microchip|
supercomputer|machine learning|neural network|quantum computing|satellite|rocket|space station|telescope|exoplanet|
asteroid|comet|eclipse|volcano|earthquake|tsunami|landslide|archaeology|excavation|fossil|dinosaur|manuscript|
cathedral|monastery|castle|lighthouse|railway|tramway|canal|suspension bridge|tunnel|airport|harbour|shipyard|
steelworks|brewery|vineyard|orchard|dairy farm|beekeeping|olive oil|cocoa|tea plantation|rice paddy|soybean|
cotton mill|wool|silk road|e-mail|covid-19|u.s.|c++|co-op|world war ii|cold war|referendum|parliament|senate|
supreme court|constitution|ballot|embassy|treaty|olympic games|world cup|marathon|chess|opera|symphony|orchestra|
ballet|film festival|sculpture|photography|tapestry|stained glass|pipe organ|lute|harpsichord|folk song|sea shanty|
cartography|atlas|almanac|printing press|typewriter|telegraph|lighthouse keeper|ferry|tugboat|submarine|zeppelin|
glider|hot air balloon|velodrome|lacrosse|curling|fencing|rowing|sailing regatta|kayak|alpine skiing|bobsleigh|
falconry|botanical garden|arboretum|herbarium|seed bank|pollinator|honeybee|bumblebee|monarch butterfly|salmon run|
whale watching|sea turtle|otter|beaver dam|wolf pack|lynx|bison|elk|caribou|puffin|albatross|penguin|krill|plankton|
kelp forest|seagrass|estuary|delta|fjord""".replace("\n", "")
CONTEXT = """policy|report|study|research|school|university|children|students|price|prices|court|ruling|law|
government|minister|company|industry|market|village|city|river|island|coast|history|century|festival|community|health|
hospital|patients|data|survey|budget|funding|project|construction|farmers|workers|tourism|visitors|water|energy|
climate|species|population|exhibition|record|award|championship|team|season|album|band|garden|museum|library|archive|
newspaper|magazine|broadcast|documentary|lecture|conference|committee|council|province|county|district|harvest|export|
import|supply|demand|shortage|regulation|license|permit|inspection|audit|lawsuit|settlement|merger|acquisition|
bankruptcy|strike|protest|treaty|census|map|museum collection|annual report""".replace("\n", "")
NONE = "video game|horoscope|lottery|casino|fan fiction|coupon code|dating site|crossword".split("|")


def make_rules(n: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    topics, context = TOPICS.split("|"), CONTEXT.split("|")
    rules = []
    for i in range(n):
        groups = [rng.sample(topics, rng.randint(1, 3)), rng.sample(context, rng.randint(2, 5))]
        if rng.random() < 0.3:
            groups.append(rng.sample(topics + context, rng.randint(1, 3)))
        rule = {"rule_id": f"bench_{i:04d}", "all": groups}
        if rng.random() < 0.2:
            rule["none"] = rng.sample(NONE, rng.randint(1, 2))
        rules.append(rule)
    return rules


def scan(py, corpus, shards, rules, out, matcher, extra=()):
    args = [py, SCAN, "scan", "--corpus", corpus, "--rules", rules, "--out", out, "--workers", "1",
            "--matcher", matcher, *extra]
    if shards:
        args += ["--shards", shards]
    return subprocess.Popen([str(x) for x in args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def finish(p, what):
    _, err = p.communicate()
    if p.returncode:
        sys.exit(f"{what} failed:\n{err[-2000:]}")


def shard_stats(out: Path) -> tuple[int, float]:
    done = [json.loads(p.read_text()) for p in (out / "shards").glob("*.done.json")]
    return sum(d["docs"] for d in done), sum(d["seconds"] for d in done)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--shards", default=None)
    ap.add_argument("--work", required=True)
    ap.add_argument("--rules", type=int, default=300)
    ap.add_argument("--matchers", default="aho,token")
    ap.add_argument("--parallel", type=int, default=0)
    ap.add_argument("--total-docs", type=float, default=563e6)
    ap.add_argument("--workers", type=int, default=32, help="workers assumed by the projection")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    rules = work / "rules.jsonl"
    rules.write_text("".join(json.dumps(r) + "\n" for r in make_rules(a.rules)))
    py = sys.executable
    results, counts = {}, {}
    for m in a.matchers.split(","):
        out = work / f"single_{m}"
        subprocess.run(["rm", "-rf", str(out)], check=True)
        t0 = time.time()
        finish(scan(py, a.corpus, a.shards, rules, out, m), f"scan ({m})")
        wall = time.time() - t0
        r = subprocess.run([py, str(SCAN), "merge", "--out", str(out), "--allow-partial"],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"merge ({m}) failed:\n{r.stderr[-2000:]}")
        docs, secs = shard_stats(out)
        c = json.loads((out / "counts.json").read_text())["rule_counts"]
        counts[m] = c
        results[m] = docs / secs
        hit = [v for v in c.values() if v]
        print(f"{m:>6}: {docs:,} docs in {secs:.1f} s of shard work ({wall:.1f} s wall with start-up): "
              f"{docs / secs:,.0f} docs/s per worker; {len(hit)}/{len(c)} rules match, {sum(c.values()):,} matches, "
              f"median {statistics.median(c.values()):.0f} per rule")
    ms = list(counts)
    for m in ms[1:]:
        diff = sum(1 for r in counts[ms[0]] if counts[ms[0]][r] != counts[m][r])
        print(f"{ms[0]} vs {m}: " + ("identical counts for every rule" if not diff else f"{diff} rules DIFFER"))
    if a.parallel:
        m = ms[0]
        procs = []
        for i in range(a.parallel):
            out = work / f"par_{i:02d}"
            subprocess.run(["rm", "-rf", str(out)], check=True)
            procs.append(scan(py, a.corpus, a.shards, rules, out, m))
        for p in procs:
            finish(p, "parallel scan")
        rates = [d / s for d, s in (shard_stats(work / f"par_{i:02d}") for i in range(a.parallel))]
        results[f"{m} x{a.parallel}"] = statistics.mean(rates)
        print(f"{m} x{a.parallel} concurrent: {statistics.mean(rates):,.0f} docs/s per worker "
              f"(min {min(rates):,.0f}, max {max(rates):,.0f})")
    for k, rate in results.items():
        print(f"projection ({k}): {a.total_docs:,.0f} docs / ({a.workers} workers x {rate:,.0f} docs/s) = "
              f"{a.total_docs / (a.workers * rate) / 3600:.2f} h")


if __name__ == "__main__":
    main()
