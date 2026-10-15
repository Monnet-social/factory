#!/usr/bin/env python3
"""Bitbucket side of the routine: prepare a review workspace for a PR, post the review back.

Env:
  BB_TOKEN   Bitbucket token (repository/workspace access token → Bearer; with BB_USER → Basic)
  BB_USER    optional, Atlassian account email for API-token Basic auth
  BB_GIT_USER  git HTTPS user (default x-token-auth for access tokens)
  BB_WORKSPACE (default monnett-social), BB_REPO (default monnett-core; --repo overrides)
  JIRA_EMAIL, JIRA_TOKEN  optional, for the ticket text

Commands:
  bb.py [--repo monnett-app] prepare --pr 123 --sha abcdef0 --workdir work   → prints READY <ws> | SKIP <reason>
  bb.py [--repo …] post --pr 123 --sha abcdef0 --review work/review.json [--dry-run]
        (posts only if REVIEW_MODE=live and the repo is enabled for live in REPOS)
  bb.py [--repo …] feedback [--since 2026-10-08]   → developer replies (useful / wrong / noise) and fixes of AI findings, as JSON
  bb.py [--repo …] reply --pr 123 --comment 456 --workdir work
        a developer replied in an AI finding thread → prints SKIP <reason> | DONE <action> (handled without the
        LLM) | JUDGE <ws> (the LLM decides per routine/REPLY.md, then calls reply-post)
  bb.py [--repo …] reply-post --pr 123 --comment 456 --decision work/decision.json [--dry-run]
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FACTORY = os.path.dirname(HERE)
WS_NAME = os.environ.get("BB_WORKSPACE", "monnett-social")
# Per repository: checkout dir in the workspace (the skill reads it), pre-check script, whether open PRs'
# Flyway migrations are collected, read-only context repos ({dir: (repo, branch)}, e.g. the backend contract
# for the app), and whether posting is allowed at all (a second gate after REVIEW_MODE, so one repo can stay
# in shadow while the shared environment is live).
REPOS = {
    "monnett-core": {"dir": "core", "precheck": "precheck.py", "flyway": True, "context": {}, "live": True},
    "monnett-app": {"dir": "app", "precheck": "precheck_app.py", "flyway": False,
                    "context": {"core": ("monnett-core", "develop")}, "live": True},
}
REPO = os.environ.get("BB_REPO", "monnett-core")
API = f"https://api.bitbucket.org/2.0/repositories/{WS_NAME}/{REPO}"
MARKER = "[//]: # (monnett-ai-review sha={sha})"
FINDING_MARKER = "[//]: # (monnett-ai-finding)"
FIXED_MARKER = "[//]: # (monnett-ai-fixed sha={sha})"
FIXED_RE = re.compile(r"\(monnett-ai-fixed sha=([0-9a-f]+)\)")
ACK_MARKER = "[//]: # (monnett-ai-ack c={cid} resolve={resolve})"
ACK_RE = re.compile(r"\(monnett-ai-ack c=(\d+) resolve=(yes|no)\)")
BOT_RE = re.compile(r"\(monnett-ai-[a-z]+[ )]")  # every comment the bot posts carries one of its markers
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
    headers = {"Authorization": auth_header(), "Accept": "application/json"}
    if data is not None:  # an empty body with a JSON content type gets 400 (e.g. /resolve)
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r) if r.length != 0 else {}


def paged(url):
    while url:
        page = api("GET", url)
        yield from page.get("values", [])
        url = page.get("next")


def comments_of(pr):
    # the list endpoint leaves `resolution` empty unless it is requested explicitly
    return list(paged(f"/pullrequests/{pr}/comments?pagelen=100&fields=%2Bvalues.resolution.*"))


def sh(*cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:  # the clone URL carries the token: never let it reach the run log
        tok = os.environ.get("BB_TOKEN") or "\0"
        raise RuntimeError(f"{' '.join(cmd)} → exit {r.returncode}: {r.stderr.strip()}".replace(tok, "***"))
    return r.stdout.strip()


def thread_replies(comments, root_id, bot=False):
    """Non-deleted replies under a comment, nested ones included, in posting order: the developers' (default) or the bot's own."""
    ids, out = {root_id}, []
    for c in sorted(comments, key=lambda c: c["id"]):
        if (c.get("parent") or {}).get("id") in ids and not c.get("deleted"):
            ids.add(c["id"])
            if bool(BOT_RE.search(c["content"]["raw"] or "")) == bot:
                out.append(c)
    return out


def is_live(dry_run=False):
    return not dry_run and os.environ.get("REVIEW_MODE") == "live" and REPOS[REPO]["live"]  # shadow unless explicitly live


def fixed_in(comments, root_id):
    """SHA at which the bot found the finding fixed and resolved its thread, else None."""
    m = next((FIXED_RE.search(r["content"]["raw"]) for r in thread_replies(comments, root_id, bot=True)), None)
    return m.group(1) if m else None


def votes_of(replies):
    return [w for r in replies for w in FEEDBACK if re.search(rf"\b{w}\b", (r["content"]["raw"] or "").lower())]


def ai_threads(comments):
    """Earlier AI comments on the PR with the developers' replies, keyed by comment id: inline findings, and
    summaries (they hold the nits and file-level items, so a re-review must not repeat those either)."""
    out = {}
    for c in comments:
        raw = (c.get("content") or {}).get("raw") or ""
        kind = "finding" if FINDING_MARKER in raw else "summary" if "(monnett-ai-review sha=" in raw else None
        if not kind or c.get("deleted") or c.get("parent"):
            continue
        replies = thread_replies(comments, c["id"])
        inline = c.get("inline") or {}
        out[c["id"]] = {"id": c["id"], "kind": kind, "path": inline.get("path"), "line": inline.get("to") or inline.get("from"),
                        "outdated": bool(inline.get("outdated")), "created_on": c.get("created_on"),
                        "text": re.sub(r"\[//\]: # \(monnett-ai-[^)]*\)", "", raw).strip(), "resolved": bool(c.get("resolution")),
                        "fixed_in": fixed_in(comments, c["id"]),
                        "votes": votes_of(replies),
                        "replies": [{"author": r["user"]["display_name"], "text": r["content"]["raw"]} for r in replies]}
    return out


def already_reviewed(pr, sha):
    mark = MARKER.format(sha=sha[:12])
    return any(mark in (c.get("content", {}).get("raw") or "") for c in comments_of(pr))


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
    if not a.force and already_reviewed(a.pr, head):
        print(f"SKIP already reviewed {head[:12]}")
        return
    ws = os.path.abspath(a.workdir)
    cfg = REPOS[REPO]
    checkout, full_head, base = checkout_head(pr, ws)
    user = os.environ.get("BB_GIT_USER", "x-token-auth")
    dest = pr["destination"]["branch"]["name"]
    src = pr["source"]["branch"]["name"]
    p = lambda n: os.path.join(ws, "pr", n)
    jira = re.search(r"MNT-\d+", pr["title"] + " " + src)
    json.dump({"pr": pr["id"], "title": pr["title"], "description": pr.get("description") or "",
               "author": pr["author"]["display_name"], "source_branch": src, "dest_branch": dest,
               "jira": jira.group(0) if jira else None, "head": full_head, "base": base},
              open(p("meta.json"), "w"), indent=1, ensure_ascii=False)
    open(p("diff.patch"), "w").write(sh("git", "diff", "-M", base, full_head, cwd=checkout))
    open(p("files.txt"), "w").write(sh("git", "diff", "-M", "--name-status", base, full_head, cwd=checkout))
    json.dump(open_pr_migrations(pr["id"]) if cfg["flyway"] else [], open(p("open-migrations.json"), "w"))
    prior = ai_threads(comments_of(a.pr))
    json.dump(list(prior.values()), open(p("prior-review.json"), "w"), indent=1, ensure_ascii=False)
    sh(sys.executable, os.path.join(FACTORY, "checks", cfg["precheck"]), "--repo", checkout, "--base", base,
       "--head", full_head, "--develop", f"origin/{dest}", "--open-migrations", p("open-migrations.json"),
       "--out", p("precheck.json"))
    if jira and os.environ.get("JIRA_TOKEN"):
        sh(sys.executable, os.path.join(FACTORY, "jira", "jira_issue.py"), jira.group(0), "--out", p("jira.md"))
    for d, (repo, branch) in cfg["context"].items():  # tree only: no history, no token left on disk
        ctx = os.path.join(ws, d)
        if not os.path.exists(ctx):
            sh("git", "clone", "--quiet", "--depth", "1", "--branch", branch,
               f"https://{user}:{os.environ['BB_TOKEN']}@bitbucket.org/{WS_NAME}/{repo}.git", ctx)
            shutil.rmtree(os.path.join(ctx, ".git"))
    print(f"READY {ws}")


def checkout_head(pr, ws):
    """Clone the repo into the workspace and detach at the PR head; returns (checkout, head, merge base)."""
    checkout = os.path.join(ws, REPOS[REPO]["dir"])
    os.makedirs(os.path.join(ws, "pr"), exist_ok=True)
    user = os.environ.get("BB_GIT_USER", "x-token-auth")
    if not os.path.exists(checkout):
        # full clone: blobs must be local, the token is dropped from the remote below
        sh("git", "clone", "--quiet", f"https://{user}:{os.environ['BB_TOKEN']}@bitbucket.org/{WS_NAME}/{REPO}.git", checkout)
    dest = pr["destination"]["branch"]["name"]
    src = pr["source"]["branch"]["name"]
    sh("git", "fetch", "--quiet", "origin", dest, src, cwd=checkout)
    full_head = sh("git", "rev-parse", f"origin/{src}", cwd=checkout)
    sh("git", "checkout", "--quiet", "--detach", full_head, cwd=checkout)
    sh("git", "remote", "set-url", "origin", f"https://bitbucket.org/{WS_NAME}/{REPO}.git", cwd=checkout)  # drop token from config
    return checkout, full_head, sh("git", "merge-base", f"origin/{dest}", full_head, cwd=checkout)


def head_line(f):
    return f"**AI · {SEV_LABEL.get(f['severity'], f['severity'])}: {f['title']}**"


def render(f):
    fix = f"\n\n**Fix:** {f['fix']}" if f.get("fix") else ""
    return (f"{head_line(f)}\n\n{f['body']}{fix}\n\n"
            f"_Reply `useful`, `wrong` or `noise`: it scores the AI reviewer pilot._\n\n{FINDING_MARKER}")


def post(a):
    rv = json.load(open(a.review))
    findings = rv.get("findings", [])
    threads = ai_threads(comments_of(a.pr))

    def prior_of(f):  # the reviewer links repeats via prior_id; the exact title is only a fallback
        try:
            pid = int(f.get("prior_id"))
        except (TypeError, ValueError):
            pid = None
        return threads.get(pid) or next((t for t in threads.values() if head_line(f) in t["text"]), None)

    fixed = []  # earlier findings the reviewer verified as fixed at this head → reply + resolve the thread
    for x in rv.get("fixed_prior", []):
        try:
            t = threads.get(int(x.get("prior_id")))
        except (TypeError, ValueError):
            t = None
        if t and t["kind"] == "finding" and not t["resolved"] and not t["fixed_in"] and x.get("evidence"):
            fixed.append((t, str(x["evidence"]).strip()))
    fixed_ids = {t["id"] for t, _ in fixed}
    answered, carried, new = [], [], []
    for f in findings:
        t = prior_of(f)
        if t and t["id"] in fixed_ids:
            continue
        # a thread the developer answered or resolved is theirs to close; the bot never repeats it
        (new if t is None else answered if t["replies"] or t["resolved"] else carried).append(f)
    inline = [f for f in new if f["severity"] in ("blocking", "should", "question") and f.get("path") and f.get("line")]
    rest = [f for f in new if f not in inline]
    counts = {s: sum(1 for f in new if f["severity"] == s) for s in SEV_LABEL}
    lines = [f"**AI review** (`{a.sha[:12]}`): " + ", ".join(f"{n} {SEV_LABEL[s].lower()}{'s' if n > 1 and s in ('nit', 'question') else ''}" for s, n in counts.items() if n)
             if new else f"**AI review** (`{a.sha[:12]}`): no new findings.", "", rv.get("summary", "")]
    if rest:
        lines += ["", *[f"- **{SEV_LABEL.get(f['severity'])}** `{f.get('path') or ''}{':' + str(f['line']) if f.get('line') else ''}` "
                        f"{f['title']}: {f['body']}" for f in rest]]
    if carried:
        lines += ["", f"{len(carried)} unanswered finding(s) from an earlier commit still apply and were not re-posted."]
    if answered:
        lines += ["", f"{len(answered)} finding(s) already answered in their threads were not repeated."]
    if fixed:
        lines += ["", f"{len(fixed)} earlier finding(s) fixed in this commit; their threads were resolved."]
    lines += ["", "_First-pass AI review against CLAUDE.md and the team rubric (pilot). Reply `useful`, `wrong` "
                  "or `noise` to any AI comment._",
              "", MARKER.format(sha=a.sha[:12])]
    summary = "\n".join(lines)
    if not is_live(a.dry_run):
        print(json.dumps({"summary": summary, "inline": [{"path": f["path"], "line": f["line"], "text": render(f)} for f in inline],
                          "carried": [f["title"] for f in carried], "answered": [f["title"] for f in answered],
                          "fixed": [{"comment": t["id"], "reply": fixed_reply(a.sha, ev)} for t, ev in fixed]},
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
    resolved = 0
    for t, ev in fixed:
        try:
            api("POST", f"/pullrequests/{a.pr}/comments", {"content": {"raw": fixed_reply(a.sha, ev)}, "parent": {"id": t["id"]}})
            api("POST", f"/pullrequests/{a.pr}/comments/{t['id']}/resolve")
            resolved += 1
        except urllib.error.HTTPError as e:
            print(f"resolve failed ({e.code}) for comment {t['id']}", file=sys.stderr)
    api("POST", f"/pullrequests/{a.pr}/comments", {"content": {"raw": summary}})
    print(f"POSTED {len(inline)} inline + summary, resolved {resolved} fixed thread(s)")


def fixed_reply(sha, evidence):
    return f"**AI · Fixed in `{sha[:12]}`:** {evidence}\n\n{FIXED_MARKER.format(sha=sha[:12])}"


def feedback(a):
    """Per AI comment: the developers' votes and replies, and whether a later commit fixed it (bot-resolved)."""
    out = []
    q = f'updated_on>={a.since}T00:00:00+00:00'
    for p in paged("/pullrequests?state=OPEN&state=MERGED&state=DECLINED&pagelen=50&q=" + urllib.parse.quote(q)):
        comments = comments_of(p['id'])
        ai = {c["id"]: c for c in comments if FINDING_MARKER in (c.get("content", {}).get("raw") or "")
              or "monnett-ai-review" in (c.get("content", {}).get("raw") or "")}
        for cid, c in ai.items():
            raw = c["content"]["raw"]
            replies = thread_replies(comments, cid)
            votes = votes_of(replies)
            out.append({"pr": p["id"], "comment": cid, "kind": "finding" if FINDING_MARKER in raw else "summary",
                        "title": raw.splitlines()[0][:120], "path": (c.get("inline") or {}).get("path"),
                        "votes": votes, "replies": len(replies),
                        "resolved": bool(c.get("resolution")), "fixed_in": fixed_in(comments, cid)})
    fs = [x for x in out if x["kind"] == "finding"]
    totals = {"findings": len(fs), "answered": sum(1 for x in fs if x["replies"]),
              **{w: sum(1 for x in fs if w in x["votes"]) for w in FEEDBACK},
              "resolved": sum(1 for x in fs if x["resolved"]), "fixed": sum(1 for x in fs if x["fixed_in"]),
              "useful_or_fixed": sum(1 for x in fs if x["fixed_in"] or "useful" in x["votes"])}
    print(json.dumps({"totals": totals, "comments": out}, indent=1, ensure_ascii=False))


LABEL_SEV = {v: k for k, v in SEV_LABEL.items()}
ANSWERED_TEXT = "**AI · Answered, thanks.** Resolving the question."
YIELD_TEXT = "**AI · Noted.** The decision is yours; resolving."


def ack_reply(cid, text, resolve):
    return f"{text}\n\n{ACK_MARKER.format(cid=cid, resolve='yes' if resolve else 'no')}"


def send_ack(pr, root_id, cid, text, resolve, dry_run=False):
    """Reply under the developer's comment (text may be empty: resolve only) and resolve the thread if asked."""
    if not is_live(dry_run):
        print(json.dumps({"dry_run": True, "thread": root_id, "reply_to": cid, "text": text, "resolve": resolve}, ensure_ascii=False))
        return
    if text:
        api("POST", f"/pullrequests/{pr}/comments", {"content": {"raw": ack_reply(cid, text, resolve)}, "parent": {"id": cid}})
    if resolve:
        api("POST", f"/pullrequests/{pr}/comments/{root_id}/resolve")


def reply_thread(pr, cid):
    """(comments, the developer's comment, root finding) or a SKIP reason."""
    comments = comments_of(pr)
    by_id = {c["id"]: c for c in comments}
    c = by_id.get(int(cid))
    if not c or c.get("deleted"):
        return "comment not found"
    if BOT_RE.search(c["content"]["raw"] or ""):
        return "own comment"
    root = c
    while (root.get("parent") or {}).get("id") in by_id:
        root = by_id[root["parent"]["id"]]
    if root is c or FINDING_MARKER not in (root["content"]["raw"] or ""):
        return "not a reply in an AI finding thread"
    if root.get("resolution"):
        return "thread already resolved"
    if any(int(m.group(1)) == c["id"] for r in thread_replies(comments, root["id"], bot=True)
           for m in [ACK_RE.search(r["content"]["raw"] or "")] if m):
        return "already answered"
    return comments, c, root


def reply(a):
    if not re.fullmatch(r"\d{1,6}", str(a.pr)) or not re.fullmatch(r"\d{1,12}", str(a.comment)):
        print("SKIP invalid payload")
        return
    pr = api("GET", f"/pullrequests/{a.pr}")
    if pr["state"] != "OPEN":
        print(f"SKIP PR state {pr['state']}")
        return
    t = reply_thread(a.pr, a.comment)
    if isinstance(t, str):
        print(f"SKIP {t}")
        return
    comments, c, root = t
    raw = root["content"]["raw"]
    m = re.match(r"\*\*AI · ([^:]+):", raw)
    severity = LABEL_SEV.get(m.group(1).strip() if m else "", "should")
    text = (c["content"]["raw"] or "").strip()
    bare = re.sub(r"[`*_.,:;!\\\s-]|" + "|".join(FEEDBACK), "", text.lower()) == ""  # only vote words left
    votes = votes_of([c])
    acks = [ACK_RE.search(r["content"]["raw"] or "") for r in thread_replies(comments, root["id"], bot=True)]
    kept_before = any(k and k.group(2) == "no" for k in acks)
    if severity == "question":
        send_ack(a.pr, root["id"], c["id"], ANSWERED_TEXT, True)
        print("DONE question answered, resolved")
    elif bare and set(votes) & {"wrong", "noise"}:
        send_ack(a.pr, root["id"], c["id"], "", True)
        print("DONE vote without explanation, resolved")
    elif bare:
        print("SKIP vote only, the thread stays open until the finding is fixed")
    elif kept_before:  # the bot kept the thread open once already: never argue a second time
        send_ack(a.pr, root["id"], c["id"], YIELD_TEXT, True)
        print("DONE second answer, resolved")
    else:
        ws = os.path.abspath(a.workdir)
        checkout, head, base = checkout_head(pr, ws)
        open(os.path.join(ws, "pr", "diff.patch"), "w").write(sh("git", "diff", "-M", base, head, cwd=checkout))
        inline = root.get("inline") or {}
        json.dump({"pr": pr["id"], "title": pr["title"], "head": head, "severity": severity,
                   "finding": re.sub(r"\[//\]: # \(monnett-ai-[^)]*\)", "", raw).strip(),
                   "path": inline.get("path"), "line": inline.get("to") or inline.get("from"),
                   "thread": [{"author": r["user"]["display_name"], "bot": bool(BOT_RE.search(r["content"]["raw"] or "")),
                               "text": re.sub(r"\[//\]: # \(monnett-ai-[^)]*\)", "", r["content"]["raw"] or "").strip()}
                              for r in sorted(thread_replies(comments, root["id"]) + thread_replies(comments, root["id"], bot=True),
                                              key=lambda r: r["id"]) if r["id"] <= c["id"]],
                   "reply": {"author": c["user"]["display_name"], "text": text}},
                  open(os.path.join(ws, "pr", "reply.json"), "w"), indent=1, ensure_ascii=False)
        print(f"JUDGE {ws}")


def reply_post(a):
    d = json.load(open(a.decision))
    t = reply_thread(a.pr, a.comment)  # re-checked: another run may have answered meanwhile
    if isinstance(t, str):
        print(f"SKIP {t}")
        return
    _, c, root = t
    text = re.sub(r"\s+", " ", str(d.get("text") or "")).strip()[:600]
    if not text:
        print("SKIP decision without text")
        return
    resolve = d.get("resolve") is True
    send_ack(a.pr, root["id"], c["id"], f"**AI · {'Resolved' if resolve else 'Still open'}:** {text}", resolve, a.dry_run)
    print(f"DONE {'resolved' if resolve else 'kept open'}")


def main():
    global REPO, API
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO, choices=sorted(REPOS))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("prepare")
    p1.add_argument("--pr", required=True)
    p1.add_argument("--sha", required=True)
    p1.add_argument("--workdir", default="work")
    p1.add_argument("--force", action="store_true", help="local testing: prepare even if this SHA was reviewed")
    p2 = sub.add_parser("post")
    p2.add_argument("--pr", required=True)
    p2.add_argument("--sha", required=True)
    p2.add_argument("--review", required=True)
    p2.add_argument("--dry-run", action="store_true")
    p3 = sub.add_parser("feedback")
    p3.add_argument("--since", default="2026-10-08")
    p4 = sub.add_parser("reply")
    p4.add_argument("--pr", required=True)
    p4.add_argument("--comment", required=True)
    p4.add_argument("--workdir", default="work")
    p5 = sub.add_parser("reply-post")
    p5.add_argument("--pr", required=True)
    p5.add_argument("--comment", required=True)
    p5.add_argument("--decision", required=True)
    p5.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    REPO, API = a.repo, f"https://api.bitbucket.org/2.0/repositories/{WS_NAME}/{a.repo}"
    {"prepare": prepare, "post": post, "feedback": feedback, "reply": reply, "reply-post": reply_post}[a.cmd](a)


if __name__ == "__main__":
    main()
