#!/usr/bin/env python3
"""Aggregate backtest results: recall vs human first-round comments, precision of AI findings, cost.

Usage: score.py --runs <dir> --selection selection.json [--out report.json]
"""
import argparse
import collections
import glob
import json
import os


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--selection", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    sel = {x["pr"]: x for x in json.load(open(a.selection))["selected"]}

    rows, hum, ai = [], [], []
    cost = dur = turns = 0.0
    for ws in sorted(glob.glob(os.path.join(a.runs, "PR-*"))):
        pr = int(ws.rsplit("-", 1)[1])
        rv, jd, run = load(f"{ws}/review.json"), load(f"{ws}/judge.json"), load(f"{ws}/run.json")
        if run:
            cost += run.get("total_cost_usd") or 0
            dur += (run.get("duration_ms") or 0) / 1000
            turns += run.get("num_turns") or 0
        if not rv:
            rows.append({"pr": pr, "status": "no review"})
            continue
        fs = rv.get("findings", [])
        sev = collections.Counter(f.get("severity") for f in fs)
        row = {"pr": pr, "src_lines": sel.get(pr, {}).get("src_lines"), "human_first_round": len(sel.get(pr, {}).get("human_first_round", [])),
               "ai_findings": len(fs), "ai_by_severity": dict(sev),
               "precheck_kept": sum(1 for f in fs if str(f.get("source", "")).startswith("precheck")),
               "verdict": rv.get("verdict")}
        if jd:
            for h in jd.get("human", []):
                h["pr"] = pr
                hum.append(h)
            src = {f["id"]: f for f in fs}
            for j in jd.get("ai", []):
                f = src.get(j.get("id"), {})
                ai.append({**j, "pr": pr, "severity": f.get("severity"), "category": f.get("category"),
                           "source": "precheck" if str(f.get("source", "")).startswith("precheck") else "llm",
                           "silent_pr": not sel.get(pr, {}).get("human_total")})
        rows.append(row)

    sub = [h for h in hum if h.get("substantive")]
    def recall(items):
        if not items:
            return None
        s = sum(1 if h["caught"] == "yes" else 0.5 if h["caught"] == "partial" else 0 for h in items)
        return round(s / len(items), 3)
    by_sev = {s: {"n": len(v), "recall": recall(v)} for s, v in group(sub, "severity").items()}
    by_cat = {c: {"n": len(v), "recall": recall(v)} for c, v in sorted(group(sub, "category").items(), key=lambda kv: -len(kv[1]))}

    def precision(items):
        if not items:
            return None
        v = collections.Counter(i["verdict"] for i in items)
        n = len(items)
        return {"n": n, **dict(v), "precision_strict": round((v["matches_human"] + v["valid"]) / n, 3),
                "precision_lenient": round((v["matches_human"] + v["valid"] + v["plausible"]) / n, 3),
                "fixed_later": sum(1 for i in items if i.get("fixed_later") == "yes"),
                "newer_rule": sum(1 for i in items if i.get("newer_rule"))}

    report = {
        "prs_reviewed": sum(1 for r in rows if r.get("ai_findings") is not None),
        "prs_judged": len({h["pr"] for h in hum} | {i["pr"] for i in ai}),
        "human_comments_substantive": len(sub),
        "recall_overall": recall(sub),
        "recall_by_human_severity": by_sev,
        "recall_by_category": by_cat,
        "ai_precision_overall": precision(ai),
        "ai_precision_by_severity": {s: precision(v) for s, v in group(ai, "severity").items()},
        "ai_precision_by_source": {s: precision(v) for s, v in group(ai, "source").items()},
        "ai_on_prs_humans_left_uncommented": precision([i for i in ai if i["silent_pr"]]),
        "findings_per_pr_mean": round(sum(r.get("ai_findings", 0) for r in rows) / max(1, len(rows)), 2),
        "cost_usd_total": round(cost, 2), "duration_s_mean": round(dur / max(1, len(rows))),
        "turns_mean": round(turns / max(1, len(rows)), 1),
        "per_pr": rows,
    }
    out = json.dumps(report, indent=1, ensure_ascii=False)
    if a.out:
        open(a.out, "w").write(out)
    print(out[:6000])


def group(items, key):
    g = collections.defaultdict(list)
    for i in items:
        g[i.get(key) or "unknown"].append(i)
    return g


if __name__ == "__main__":
    main()
