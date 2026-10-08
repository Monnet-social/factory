#!/usr/bin/env python3
"""Bitbucket side of the routine: prepare a review workspace for a PR, post the review back.

Env:
  BB_TOKEN   Bitbucket token (repository/workspace access token → Bearer; with BB_USER → Basic)
  BB_USER    optional, Atlassian account email for API-token Basic auth
  BB_GIT_USER  git HTTPS user (default x-token-auth for access tokens)
  BB_WORKSPACE (default monnett-social), BB_REPO (default monnett-core)
  JIRA_EMAIL, JIRA_TOKEN  optional, for the ticket text

Commands:
  bb.py prepare --pr 123 --sha abcdef0 --workdir work   → prints READY <ws> | SKIP <reason>
  bb.py post --pr 123 --sha abcdef0 --review work/review.json [--dry-run]   (posts only if REVIEW_MODE=live)
  bb.py feedback [--since 2026-10-08]   → developer replies (useful / wrong / noise) to AI comments, as JSON
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FACTORY = os.path.dirname(HERE)
WS_NAME = os.environ.get("BB_WORKSPACE", "monnett-social")
REPO = os.environ.get("BB_REPO", "monnett-core")
API = f"https://api.bitbucket.org/2.0/repositories/{WS_NAME}/{REPO}"
MARKER = "[//]: # (monnett-ai-review sha={sha})"
FINDING_MARKER = "[//]: # (monnett-ai-finding)"
FEEDBACK = ("useful", "wrong", "noise")
SEV_LABEL = {"blocking": "Blocking", "should": "Should fix", "nit": "Nit", "question": "Question"}


def auth_header():
    tok = os.environ["BB_TOKEN"]
    if os.environ.get("BB_USER"):
        return "Basic " + base64.b64encode(f"{os.environ['BB_USER']}:{tok}".encode()).decode()
    return f"Bearer {tok}"


def api(method, url, body=None):
    url = url if url.startswith("http") else API + url
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": auth_header(), "Accept": "application/json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r) if r.length != 0 else {}


def paged(url):
    while url:
        page = api("GET", url)
        yield from page.get("values", [])
        url = page.get("next")


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def thread_replies(comments, root_id):
    """All non-deleted replies under a comment, nested ones included, in posting order."""
    ids, out = {root_id}, []
    for c in sorted(comments, key=lambda c: c["id"]):
        if (c.get("parent") or {}).get("id") in ids and not c.get("deleted"):
            ids.add(c["id"])
            out.append(c)
    return out


def votes_of(replies):
    return [w for r in replies for w in FEEDBACK if re.search(rf"\b{w}\b", (r["content"]["raw"] or "").lower())]


def ai_threads(comments):
    """Earlier AI inline findings on the PR with the developers' replies, keyed by comment id."""
    out = {}
    for c in comments:
        raw = (c.get("content") or {}).get("raw") or ""
        if FINDING_MARKER not in raw or c.get("deleted") or c.get("parent"):
            continue
        replies = thread_replies(comments, c["id"])
        inline = c.get("inline") or {}
        out[c["id"]] = {"id": c["id"], "path": inline.get("path"), "line": inline.get("to") or inline.get("from"),
                        "outdated": bool(inline.get("outdated")), "created_on": c.get("created_on"),
                        "text": raw.replace(FINDING_MARKER, "").strip(), "resolved": bool(c.get("resolution")),
                        "votes": votes_of(replies),
                        "replies": [{"author": r["user"]["display_name"], "text": r["content"]["raw"]} for r in replies]}
    return out


def already_reviewed(pr, sha):
    mark = MARKER.format(sha=sha[:12])
    return any(mark in (c.get("content", {}).get("raw") or "") for c in paged(f"/pullrequests/{pr}/comments?pagelen=100"))


def open_pr_migrations(exclude):
    out = []
    for p in paged("/pullrequests?state=OPEN&pagelen=50"):
        if p["id"] == exclude:
            continue
        files = [d["new"]["path"].rsplit("/", 1)[1] for d in paged(f"/pullrequests/{p['id']}/diffstat?pagelen=100")
                 if d.get("status") == "added" and d.get("new") and "db/migration/" in d["new"]["path"]]
        if files:
            out.append({"pr": p["id"], "files": files})
    return out


def prepare(a):
    if not re.fullmatch(r"\d{1,6}", str(a.pr)) or not re.fullmatch(r"[0-9a-f]{7,40}", a.sha):
        print("SKIP invalid payload")
        return
    pr = api("GET", f"/pullrequests/{a.pr}")
    if pr["state"] != "OPEN":
        print(f"SKIP PR state {pr['state']}")
        return
    head = pr["source"]["commit"]["hash"]
    if not head.startswith(a.sha[:12]) and not a.sha.startswith(head[:12]):
        print(f"SKIP PR head moved to {head[:12]} (a newer run will review it)")
        return
    if already_reviewed(a.pr, head):
        print(f"SKIP already reviewed {head[:12]}")
        return
    ws = os.path.abspath(a.workdir)
    core = os.path.join(ws, "core")
    os.makedirs(os.path.join(ws, "pr"), exist_ok=True)
    user = os.environ.get("BB_GIT_USER", "x-token-auth")
    url = f"https://{user}:{os.environ['BB_TOKEN']}@bitbucket.org/{WS_NAME}/{REPO}.git"
    if not os.path.exists(core):
        # full clone (~13 MB): blobs must be local, the token is dropped from the remote below
        sh("git", "clone", "--quiet", url, core)
    dest = pr["destination"]["branch"]["name"]
    src = pr["source"]["branch"]["name"]
    sh("git", "fetch", "--quiet", "origin", dest, src, cwd=core)
    full_head = sh("git", "rev-parse", f"origin/{src}", cwd=core)
    sh("git", "checkout", "--quiet", "--detach", full_head, cwd=core)
    sh("git", "remote", "set-url", "origin", f"https://bitbucket.org/{WS_NAME}/{REPO}.git", cwd=core)  # drop token from config
    base = sh("git", "merge-base", f"origin/{dest}", full_head, cwd=core)
    p = lambda n: os.path.join(ws, "pr", n)
    jira = re.search(r"MNT-\d+", pr["title"] + " " + src)
    json.dump({"pr": pr["id"], "title": pr["title"], "description": pr.get("description") or "",
               "author": pr["author"]["display_name"], "source_branch": src, "dest_branch": dest,
               "jira": jira.group(0) if jira else None, "head": full_head, "base": base},
              open(p("meta.json"), "w"), indent=1, ensure_ascii=False)
    open(p("diff.patch"), "w").write(sh("git", "diff", "-M", base, full_head, cwd=core))
    open(p("files.txt"), "w").write(sh("git", "diff", "-M", "--name-status", base, full_head, cwd=core))
    json.dump(open_pr_migrations(pr["id"]), open(p("open-migrations.json"), "w"))
    prior = ai_threads(list(paged(f"/pullrequests/{a.pr}/comments?pagelen=100")))
    json.dump(list(prior.values()), open(p("prior-review.json"), "w"), indent=1, ensure_ascii=False)
    sh(sys.executable, os.path.join(FACTORY, "checks", "precheck.py"), "--repo", core, "--base", base,
       "--head", full_head, "--develop", f"origin/{dest}", "--open-migrations", p("open-migrations.json"),
       "--out", p("precheck.json"))
    if jira and os.environ.get("JIRA_TOKEN"):
        sh(sys.executable, os.path.join(FACTORY, "jira", "jira_issue.py"), jira.group(0), "--out", p("jira.md"))
    print(f"READY {ws}")


def head_line(f):
    return f"**AI · {SEV_LABEL.get(f['severity'], f['severity'])}: {f['title']}**"


def render(f):
    fix = f"\n\n**Fix:** {f['fix']}" if f.get("fix") else ""
    return (f"{head_line(f)}\n\n{f['body']}{fix}\n\n"
            f"_Reply `useful`, `wrong` or `noise` — it scores the AI reviewer pilot._\n\n{FINDING_MARKER}")


def post(a):
    rv = json.load(open(a.review))
    findings = rv.get("findings", [])
    threads = ai_threads(list(paged(f"/pullrequests/{a.pr}/comments?pagelen=100")))

    def prior_of(f):  # the reviewer links repeats via prior_id; the exact title is only a fallback
        try:
            pid = int(f.get("prior_id"))
        except (TypeError, ValueError):
            pid = None
        return threads.get(pid) or next((t for t in threads.values() if head_line(f) in t["text"]), None)

    answered, carried, new = [], [], []
    for f in findings:
        t = prior_of(f)
        # a thread the developer answered or resolved is theirs to close; the bot never repeats it
        (new if t is None else answered if t["replies"] or t["resolved"] else carried).append(f)
    inline = [f for f in new if f["severity"] in ("blocking", "should", "question") and f.get("path") and f.get("line")]
    rest = [f for f in new if f not in inline]
    counts = {s: sum(1 for f in new if f["severity"] == s) for s in SEV_LABEL}
    lines = [f"**AI review** (`{a.sha[:12]}`) — " + ", ".join(f"{n} {SEV_LABEL[s].lower()}{'s' if n > 1 and s in ('nit', 'question') else ''}" for s, n in counts.items() if n)
             if new else f"**AI review** (`{a.sha[:12]}`) — no new findings.", "", rv.get("summary", "")]
    if rest:
        lines += ["", *[f"- **{SEV_LABEL.get(f['severity'])}** `{f.get('path') or ''}{':' + str(f['line']) if f.get('line') else ''}` "
                        f"{f['title']}: {f['body']}" for f in rest]]
    if carried:
        lines += ["", f"{len(carried)} unanswered finding(s) from an earlier commit still apply and were not re-posted."]
    if answered:
        lines += ["", f"{len(answered)} finding(s) already answered in their threads were not repeated."]
    lines += ["", "_First-pass AI review against CLAUDE.md and the team rubric (pilot). Reply `useful`, `wrong` "
                  "or `noise` to any AI comment._",
              "", MARKER.format(sha=a.sha[:12])]
    summary = "\n".join(lines)
    if a.dry_run or os.environ.get("REVIEW_MODE") != "live":  # shadow unless explicitly live
        print(json.dumps({"summary": summary, "inline": [{"path": f["path"], "line": f["line"], "text": render(f)} for f in inline],
                          "carried": [f["title"] for f in carried], "answered": [f["title"] for f in answered]},
                         indent=1, ensure_ascii=False))
        return
    for f in inline:
        try:
            api("POST", f"/pullrequests/{a.pr}/comments",
                {"content": {"raw": render(f)}, "inline": {"path": f["path"], "to": int(f["line"])}})
        except urllib.error.HTTPError as e:  # line outside the diff → fold into the summary
            summary = summary.replace(MARKER.format(sha=a.sha[:12]),
                                      f"- `{f['path']}:{f['line']}` {render(f).replace(FINDING_MARKER, '').strip()}\n\n" + MARKER.format(sha=a.sha[:12]))
            print(f"inline failed ({e.code}) for {f['path']}:{f['line']}, folded into summary", file=sys.stderr)
    api("POST", f"/pullrequests/{a.pr}/comments", {"content": {"raw": summary}})
    print(f"POSTED {len(inline)} inline + summary")


def feedback(a):
    out = []
    q = f'updated_on>={a.since}T00:00:00+00:00'
    for p in paged("/pullrequests?state=OPEN&state=MERGED&state=DECLINED&pagelen=50&q=" + urllib.parse.quote(q)):
        comments = list(paged(f"/pullrequests/{p['id']}/comments?pagelen=100"))
        ai = {c["id"]: c for c in comments if FINDING_MARKER in (c.get("content", {}).get("raw") or "")
              or "monnett-ai-review" in (c.get("content", {}).get("raw") or "")}
        for cid, c in ai.items():
            raw = c["content"]["raw"]
            replies = thread_replies(comments, cid)
            votes = votes_of(replies)
            out.append({"pr": p["id"], "comment": cid, "kind": "finding" if FINDING_MARKER in raw else "summary",
                        "title": raw.splitlines()[0][:120], "path": (c.get("inline") or {}).get("path"),
                        "votes": votes, "replies": len(replies),
                        "resolved": bool(c.get("resolution"))})
    print(json.dumps(out, indent=1, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("prepare")
    p1.add_argument("--pr", required=True)
    p1.add_argument("--sha", required=True)
    p1.add_argument("--workdir", default="work")
    p2 = sub.add_parser("post")
    p2.add_argument("--pr", required=True)
    p2.add_argument("--sha", required=True)
    p2.add_argument("--review", required=True)
    p2.add_argument("--dry-run", action="store_true")
    p3 = sub.add_parser("feedback")
    p3.add_argument("--since", default="2026-10-08")
    a = ap.parse_args()
    {"prepare": prepare, "post": post, "feedback": feedback}[a.cmd](a)


if __name__ == "__main__":
    main()
