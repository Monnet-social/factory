#!/usr/bin/env python3
"""Fetch a monnett.atlassian.net Jira issue as Markdown (summary, status, description, AC).

Env: JIRA_EMAIL, JIRA_TOKEN (Atlassian API token), JIRA_BASE (default https://monnett.atlassian.net).
Usage: jira_issue.py MNT-123 [--out file.md]
Exit 0 with a note in the output if the issue cannot be read, so a review never fails on Jira.
"""
import argparse
import base64
import json
import os
import sys
import urllib.request


def adf_text(node, depth=0):
    """Atlassian Document Format → plain Markdown-ish text."""
    if node is None:
        return ""
    if isinstance(node, list):
        return "".join(adf_text(n, depth) for n in node)
    t = node.get("type")
    kids = node.get("content", [])
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    if t == "paragraph":
        return adf_text(kids, depth) + "\n\n"
    if t == "heading":
        return "#" * (node.get("attrs", {}).get("level", 2) + 1) + " " + adf_text(kids, depth) + "\n\n"
    if t in ("bulletList", "orderedList"):
        out = []
        for i, item in enumerate(kids, 1):
            mark = f"{i}." if t == "orderedList" else "-"
            body = adf_text(item.get("content", []), depth + 1).strip().replace("\n\n", "\n")
            out.append("  " * depth + f"{mark} {body}")
        return "\n".join(out) + "\n\n"
    if t == "codeBlock":
        return "```\n" + adf_text(kids, depth) + "\n```\n\n"
    if t in ("mention", "emoji", "inlineCard"):
        a = node.get("attrs", {})
        return a.get("text") or a.get("shortName") or a.get("url") or ""
    if t == "table":
        return "\n".join(" | ".join(adf_text(c.get("content", []), depth).strip() for c in row.get("content", []))
                         for row in kids) + "\n\n"
    return adf_text(kids, depth)


def fetch(key):
    base = os.environ.get("JIRA_BASE", "https://monnett.atlassian.net")
    auth = base64.b64encode(f"{os.environ['JIRA_EMAIL']}:{os.environ['JIRA_TOKEN']}".encode()).decode()
    req = urllib.request.Request(f"{base}/rest/api/3/issue/{key}?fields=summary,status,description,issuetype,parent,customfield_*",
                                 headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def to_markdown(key, issue):
    f = issue["fields"]
    lines = [f"# {key}: {f.get('summary', '')}",
             f"Type: {(f.get('issuetype') or {}).get('name')} · Status: {(f.get('status') or {}).get('name')}"
             + (f" · Parent: {f['parent']['key']}" if f.get("parent") else ""), "",
             "## Description", "", adf_text(f.get("description")).strip() or "(empty)"]
    # Text-like custom fields (e.g. acceptance criteria) when present.
    for k, v in f.items():
        if k.startswith("customfield_") and isinstance(v, dict) and v.get("type") == "doc":
            lines += ["", f"## {k}", "", adf_text(v).strip()]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        md = to_markdown(a.key, fetch(a.key))
    except Exception as e:  # never fail the review because Jira is unreachable
        md = f"# {a.key}\n\n(Jira issue could not be read: {type(e).__name__})\n"
    if a.out:
        open(a.out, "w").write(md)
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
