#!/usr/bin/env python3
"""Build one isolated review workspace per selected PR (same layout the routine produces).

<runs>/PR-<id>/
  .claude/skills/monnett-core-review/   copy of the skill
  core/                                 tree at the review-point head, no .git (no future history)
  pr/meta.json pr/diff.patch pr/files.txt pr/precheck.json pr/jira.md

Human comments are NOT copied into the workspace; the judge reads them from selection.json.
Env for Jira: JIRA_EMAIL, JIRA_TOKEN.
Usage: prepare.py --selection selection.json --repo <core repo> --runs <dir> [--only 256,193]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FACTORY = os.path.dirname(HERE)
SKILL = os.path.join(FACTORY, ".claude", "skills", "monnett-core-review")
PRECHECK = os.path.join(FACTORY, "checks", "precheck.py")
JIRA = os.path.join(FACTORY, "jira", "jira_issue.py")


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def build(x, repo, runs):
    ws = os.path.join(runs, f"PR-{x['pr']}")
    if os.path.exists(ws):
        shutil.rmtree(ws)
    os.makedirs(os.path.join(ws, "pr"))
    os.makedirs(os.path.join(ws, "core"))
    shutil.copytree(SKILL, os.path.join(ws, ".claude", "skills", "monnett-core-review"))
    archive = subprocess.Popen(["git", "-C", repo, "archive", x["head"]], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", os.path.join(ws, "core")], stdin=archive.stdout, check=True)
    archive.wait()
    p = lambda *a: os.path.join(ws, "pr", *a)
    json.dump({k: x[k] for k in ("pr", "title", "description", "author", "source_branch", "dest_branch", "jira")},
              open(p("meta.json"), "w"), indent=1, ensure_ascii=False)
    open(p("diff.patch"), "w").write(sh(["git", "-C", repo, "diff", "-M", x["base"], x["head"]]))
    open(p("files.txt"), "w").write(sh(["git", "-C", repo, "diff", "-M", "--name-status", x["base"], x["head"]]))
    om = p("open-migrations.json")
    json.dump(x.get("open_migrations", []), open(om, "w"))
    sh([sys.executable, "-I", PRECHECK, "--repo", repo, "--base", x["base"], "--head", x["head"],
        "--develop", x["dest_commit"], "--open-migrations", om, "--out", p("precheck.json")])
    os.remove(om)
    if x.get("jira"):
        sh([sys.executable, "-I", JIRA, x["jira"], "--out", p("jira.md")])
    return ws


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selection", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--runs", required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = {int(i) for i in a.only.split(",") if i}
    for x in json.load(open(a.selection))["selected"]:
        if only and x["pr"] not in only:
            continue
        ws = build(x, a.repo, a.runs)
        n = len(json.load(open(os.path.join(ws, "pr", "precheck.json")))["findings"])
        print(f"PR-{x['pr']}: {x['src_lines']} src lines, {n} pre-check findings, jira={x.get('jira')}")


if __name__ == "__main__":
    main()
