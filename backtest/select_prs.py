#!/usr/bin/env python3
"""Select historical monnett-core PRs for the reviewer backtest.

For each PR, reconstruct the "review point": the source/destination commits at the moment the
first human (non-author) comment was written, so the AI sees exactly what that reviewer saw.

Input:  a Bitbucket dump dir with detail/<id>.json, activity/<id>_<page>.json,
        comments/<id>_<page>.json (raw API pages), and a local core git repo.
Output: selection.json (PR list with commits, size and the first-round human comments).

Usage: select_prs.py --raw <dump dir> --repo <core repo> --out <file> [--n-commented 30] [--n-silent 10]
"""
import argparse
import glob
import json
import random
import re
import subprocess
from datetime import datetime

MAX_LINES = 4000          # src lines changed; larger PRs are reported separately, not reviewed
NOISE = re.compile(r"^\s*(lgtm|looks good|approved?|thanks|ok|👍|done|fixed)\W*$", re.I)


def pages(raw, kind, pr):
    vals = []
    for p in sorted(glob.glob(f"{raw}/{kind}/{pr}_*.json"), key=lambda s: int(s.rsplit("_", 1)[1][:-5])):
        vals += json.load(open(p)).get("values", [])
    return vals


def ts(s):
    return datetime.fromisoformat(s)


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def has_commit(repo, h):
    return git(repo, "cat-file", "-e", f"{h}^{{commit}}") is not None


def src_lines(repo, base, head):
    out = git(repo, "diff", "--numstat", base, head, "--", "src/") or ""
    n = 0
    for l in out.splitlines():
        a, d, _ = l.split("\t", 2)
        if a != "-":
            n += int(a) + int(d)
    return n


def migrations_added(repo, base, head):
    out = git(repo, "diff", "--name-only", "--diff-filter=A", base, head, "--", "src/main/resources/db/migration/") or ""
    return [l.rsplit("/", 1)[1] for l in out.splitlines() if l]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-commented", type=int, default=30)
    ap.add_argument("--n-silent", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--include", default="", help="comma-separated PR ids to output as-is (no sampling)")
    a = ap.parse_args()

    prs, skipped = [], []
    for p in sorted(glob.glob(f"{a.raw}/detail/*.json")):
        d = json.load(open(p))
        pid = d["id"]
        dest = d["destination"]["branch"]["name"]
        if dest == "main" or d["state"] == "OPEN":
            continue
        author = d["author"].get("uuid") or d["author"]["display_name"]
        comments = [c for c in pages(a.raw, "comments", pid)
                    if not c.get("deleted") and c.get("user")
                    and (c["user"].get("uuid") or c["user"]["display_name"]) != author]
        updates = sorted(((ts(v["update"]["date"]), v["update"]) for v in pages(a.raw, "activity", pid) if "update" in v),
                         key=lambda x: x[0])
        if not updates:
            skipped.append((pid, "no activity updates"))
            continue
        human = [c for c in comments if not NOISE.match(c["content"]["raw"] or "")]
        if human:
            t_first = min(ts(c["created_on"]) for c in human)
            before = [u for t, u in updates if t <= t_first]
            point = before[-1] if before else updates[0][1]
            later = [t for t, _ in updates if t > t_first]
            t_next = later[0] if later else None
        else:
            point, t_first, t_next = updates[-1][1], None, None
        head = point["source"]["commit"]["hash"]
        dest_commit = point["destination"]["commit"]["hash"]
        final_head = d["source"]["commit"]["hash"]
        if not (has_commit(a.repo, head) and has_commit(a.repo, dest_commit)):
            skipped.append((pid, "review-point commit not in local repo (rebased/force-pushed?)"))
            continue
        head = git(a.repo, "rev-parse", head)
        dest_commit = git(a.repo, "rev-parse", dest_commit)
        base = git(a.repo, "merge-base", dest_commit, head)
        size = src_lines(a.repo, base, head)
        first_round = [c for c in human if t_next is None or ts(c["created_on"]) < t_next]
        jira = re.search(r"MNT-\d+", d["title"] + " " + d["source"]["branch"]["name"])
        prs.append({
            "pr": pid, "title": d["title"], "author": d["author"]["display_name"],
            "description": point.get("description") or d.get("description") or "",
            "dest_branch": dest, "source_branch": d["source"]["branch"]["name"],
            "jira": jira.group(0) if jira else None, "state": d["state"],
            "created_on": d["created_on"], "closed_on": d.get("updated_on"),
            "review_point": t_first.isoformat() if t_first else None,
            "head": head, "base": base, "dest_commit": dest_commit,
            "final_head": git(a.repo, "rev-parse", final_head) if has_commit(a.repo, final_head) else None,
            "src_lines": size, "migrations": migrations_added(a.repo, base, head),
            "human_first_round": [{
                "id": c["id"], "user": c["user"]["display_name"], "created_on": c["created_on"],
                "path": (c.get("inline") or {}).get("path"), "line": (c.get("inline") or {}).get("to"),
                "parent": (c.get("parent") or {}).get("id"), "text": c["content"]["raw"],
            } for c in first_round],
            "human_total": len(human),
        })

    # other PRs open at each review point, with the migrations they add (Flyway collision check)
    for x in prs:
        t = ts(x["review_point"] or x["created_on"])
        x["open_migrations"] = [{"pr": o["pr"], "files": o["migrations"]} for o in prs
                                if o["pr"] != x["pr"] and o["migrations"]
                                and ts(o["created_on"]) <= t <= ts(o["closed_on"])]

    if a.include:
        want = {int(i) for i in a.include.split(",")}
        json.dump({"selected": [x for x in prs if x["pr"] in want]}, open(a.out, "w"), indent=1, ensure_ascii=False)
        print("included", sorted(x["pr"] for x in prs if x["pr"] in want))
        return

    eligible = [x for x in prs if 0 < x["src_lines"] <= MAX_LINES]
    commented = [x for x in eligible if x["human_first_round"]]
    silent = [x for x in eligible if not x["human_total"]]
    rnd = random.Random(a.seed)
    pick = rnd.sample(commented, min(a.n_commented, len(commented))) + rnd.sample(silent, min(a.n_silent, len(silent)))
    pick.sort(key=lambda x: x["pr"])
    json.dump({"selected": pick,
               "stats": {"candidates": len(prs), "eligible": len(eligible), "commented": len(commented),
                         "silent": len(silent), "too_large": [x["pr"] for x in prs if x["src_lines"] > MAX_LINES],
                         "skipped": skipped}},
              open(a.out, "w"), indent=1, ensure_ascii=False)
    print(f"candidates={len(prs)} eligible={len(eligible)} commented={len(commented)} silent={len(silent)} "
          f"selected={len(pick)} skipped={len(skipped)} too_large={sum(1 for x in prs if x['src_lines'] > MAX_LINES)}")


if __name__ == "__main__":
    main()
