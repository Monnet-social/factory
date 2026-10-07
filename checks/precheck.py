#!/usr/bin/env python3
"""Deterministic pre-checks for a monnett-core pull request.

Looks only at what the PR adds or changes (diff base..head), never at legacy code.
Output: JSON list of findings {check, severity, path, line, message}.
Severity: blocking | should | nit.

Usage:
  precheck.py --repo <core git dir> --base <sha> --head <sha>
              [--develop <ref>] [--open-migrations <json>] [--out <file>]
--develop:          state of the target branch the PR will merge into (Flyway collisions).
--open-migrations:  JSON [{"pr": 123, "files": ["V0031__x.sql", ...]}] of other open PRs.
"""
import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

MIGRATION_DIR = "src/main/resources/db/migration/"
MAIN_JAVA = "src/main/java/"
TEST_JAVA = "src/test/java/"
PKG = "com/monnet/social/core/"
HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
SECURITY_ANNOTATIONS = ("@RolesAllowed", "@PermitAll", "@Authenticated", "@DenyAll")
JAVAX_ALLOWED = ("javax.crypto", "javax.imageio", "javax.sql", "javax.net", "javax.security.auth",
                 "javax.xml", "javax.naming", "javax.management")
# Mocks CLAUDE.md sanctions: the JobRunr boundary.
MOCK_ALLOWED = {"AsyncJobScheduler", "JobScheduler", "JobRequestScheduler"}


# ---------------------------------------------------------------- git helpers

def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def show(repo, rev, path):
    r = subprocess.run(["git", "-C", repo, "show", f"{rev}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def ls_tree(repo, rev, path):
    out = git(repo, "ls-tree", "-r", "--name-only", rev, "--", path, check=False)
    return [l for l in out.splitlines() if l]


@dataclass
class FileDiff:
    path: str
    status: str                      # A, M, D, R
    old_path: str = None
    added: dict = field(default_factory=dict)    # new line number -> text
    removed: list = field(default_factory=list)  # removed texts


def parse_diff(repo, base, head):
    files = {}
    for line in git(repo, "diff", "--name-status", "-M", base, head).splitlines():
        parts = line.split("\t")
        st = parts[0][0]
        if st == "R":
            files[parts[2]] = FileDiff(parts[2], "R", old_path=parts[1])
        else:
            files[parts[1]] = FileDiff(parts[1], st)
    cur, new_ln = None, 0
    for line in git(repo, "diff", "-M", "--unified=0", "--no-color", base, head).splitlines():
        if line.startswith("+++ "):
            p = line[4:]
            cur = files.get(p[2:]) if p != "/dev/null" else None
        elif line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            new_ln = int(m.group(1))
        elif cur is not None and line.startswith("+"):
            cur.added[new_ln] = line[1:]
            new_ln += 1
        elif cur is not None and line.startswith("-"):
            cur.removed.append(line[1:])
    return files


# ---------------------------------------------------------------- findings

class Findings:
    def __init__(self):
        self.items = []

    def add(self, check, severity, path, line, message):
        self.items.append({"check": check, "severity": severity, "path": path,
                           "line": line, "message": message})


# ---------------------------------------------------------------- Flyway

MIG_NAME = re.compile(r"^V(\d+)__([a-z0-9_]+)\.sql$")


def strip_sql(sql):
    sql = re.sub(r"\$(\w*)\$.*?\$\1\$", " $BODY$ ", sql, flags=re.S)   # function/trigger bodies
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return sql


def line_of(text, needle_pos):
    return text.count("\n", 0, needle_pos) + 1


def check_flyway(repo, files, head, develop, open_migrations, f):
    dev_files = [p.rsplit("/", 1)[1] for p in ls_tree(repo, develop, MIGRATION_DIR)]
    dev_versions = {}
    for n in dev_files:
        m = MIG_NAME.match(n)
        if m:
            dev_versions[int(m.group(1))] = n
    dev_max = max(dev_versions) if dev_versions else 0

    new_migs = []
    for d in files.values():
        if not d.path.startswith(MIGRATION_DIR) or not d.path.endswith(".sql"):
            continue
        name = d.path.rsplit("/", 1)[1]
        if d.status in ("M", "D") or (d.status == "R" and d.old_path.rsplit("/", 1)[1] in dev_files):
            if name in dev_files or (d.old_path and d.old_path.rsplit("/", 1)[1] in dev_files):
                f.add("flyway-edit-applied", "blocking", d.path, None,
                      f"Changes migration `{name}`, which already exists on the target branch. "
                      "Flyway validates checksums at startup; edit nothing that may be applied — add a new migration.")
            continue
        if d.status not in ("A", "R"):
            continue
        m = MIG_NAME.match(name)
        if not m:
            f.add("flyway-name", "should", d.path, None,
                  f"`{name}` does not match `V0XXX__lower_snake_description.sql`.")
            continue
        new_migs.append((int(m.group(1)), name, d))

    for ver, name, d in sorted(new_migs):
        if ver in dev_versions and dev_versions[ver] != name:
            f.add("flyway-collision", "blocking", d.path, None,
                  f"Version {ver} already exists on the target branch as `{dev_versions[ver]}`. "
                  f"Two migrations with one version make Flyway abort at startup. Next free version: V{dev_max + 1:04d}.")
        elif ver <= dev_max:
            f.add("flyway-out-of-order", "blocking", d.path, None,
                  f"Version {ver} is below the target branch's highest migration V{dev_max:04d}; "
                  f"with out-of-order disabled Flyway rejects it. Rename to V{dev_max + 1:04d}.")
    expected = dev_max + 1
    for ver, name, d in sorted(new_migs):
        if ver > dev_max:
            if ver != expected:
                f.add("flyway-gap", "should", d.path, None,
                      f"Expected V{expected:04d} (CLAUDE.md: exactly one higher than the current highest), found V{ver:04d}.")
            expected = ver + 1
    for other in open_migrations or []:
        for oname in other.get("files", []):
            om = MIG_NAME.match(oname)
            if not om:
                continue
            for ver, name, d in new_migs:
                if int(om.group(1)) == ver and oname != name:
                    f.add("flyway-collision-open-pr", "should", d.path, None,
                          f"Open PR #{other['pr']} also adds version {ver} (`{oname}`). Whoever merges second must renumber.")

    for ver, name, d in new_migs:
        text = show(repo, head, d.path) or ""
        lint_sql(d.path, text, f)


def lint_sql(path, text, f):
    clean = strip_sql(text)
    pos = 0
    for stmt in clean.split(";"):
        start = text.find(stmt.strip()[:40]) if stmt.strip() else -1
        ln = line_of(text, start) if start >= 0 else None
        s = " ".join(stmt.split())
        u = s.upper()
        pos += len(stmt) + 1
        if not s:
            continue
        if re.search(r"\bADD\s+COLUMN\b(?!\s+IF\s+NOT\s+EXISTS)", u):
            f.add("flyway-idempotent", "should", path, ln, "`ADD COLUMN` without `IF NOT EXISTS` (CLAUDE.md: migrations must be safe to re-run).")
        if re.match(r"CREATE\s+(UNIQUE\s+)?INDEX\b(?!\s+(CONCURRENTLY\s+)?IF\s+NOT\s+EXISTS)", u):
            f.add("flyway-idempotent", "should", path, ln, "`CREATE INDEX` without `IF NOT EXISTS`.")
        if re.match(r"CREATE\s+TABLE\b(?!\s+IF\s+NOT\s+EXISTS)", u):
            f.add("flyway-idempotent", "should", path, ln, "`CREATE TABLE` without `IF NOT EXISTS`.")
        if re.search(r"\bDROP\s+(TABLE|COLUMN|INDEX|TYPE|CONSTRAINT|VIEW|FUNCTION|TRIGGER)\b(?!\s+IF\s+EXISTS)", u):
            f.add("flyway-idempotent", "should", path, ln, "`DROP …` without `IF EXISTS`.")
        if re.match(r"UPDATE\s+\S+\s+SET\b", u) and " WHERE " not in f" {u} ":
            f.add("flyway-unbounded-update", "should", path, ln,
                  "`UPDATE` without `WHERE` rewrites every row in one Flyway transaction; bound it or confirm it is intended.")
        if re.match(r"DELETE\s+FROM\s+\S+$", u) or (re.match(r"DELETE\s+FROM\b", u) and " WHERE " not in f" {u} "):
            f.add("flyway-unbounded-delete", "blocking", path, ln, "`DELETE` without `WHERE` removes every row.")
        m = re.match(r"(?:ALTER\s+TABLE(?:\s+IF\s+EXISTS)?(?:\s+ONLY)?|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+([\w.\"]+)", u)
        if m and "." not in m.group(1) and "SET SEARCH_PATH" not in clean.upper():
            f.add("flyway-schema", "nit", path, ln, f"`{m.group(1).lower()}` is not schema-qualified; DDL operates on `v1.`.")
        if re.match(r"CREATE\s+(UNIQUE\s+)?INDEX\b", u) and "CONCURRENTLY" not in u:
            if re.search(r"\bON\s+(V1\.)?(POST|USER_ACCOUNT|NOTIFICATION|COMMENT|MEDIA_FILE|POST_REACTION|USER_RELATIONSHIP)\b", u):
                f.add("flyway-index-lock", "nit", path, ln,
                      "Plain `CREATE INDEX` on a large table holds a write lock for the whole build; check the table size in PRD.")


# ---------------------------------------------------------------- endpoints / Bruno

def extract_endpoints(text):
    """Return list of (method, path, decl_line, annotation_block_text, class_block_text)."""
    if not text or "@Path" not in text:
        return []
    lines = text.splitlines()
    class_path, class_annots, pending = "", [], []
    in_class = False
    out = []
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if not in_class:
            if s.startswith("@"):
                class_annots.append(s)
            if re.search(r"\b(class|interface)\s+\w+", s) and not s.startswith("*") and not s.startswith("//"):
                in_class = True
                for a in class_annots:
                    m = re.match(r'@Path\(\s*"([^"]*)"', a)
                    if m:
                        class_path = m.group(1)
            continue
        if s.startswith("@"):
            pending.append((i, s))
            continue
        if not s or s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        if pending:
            ann = [a for _, a in pending]
            methods = [a[1:].split("(")[0] for a in ann if a[1:].split("(")[0] in HTTP_METHODS]
            if methods:
                mpath = ""
                for a in ann:
                    m = re.match(r'@Path\(\s*"([^"]*)"', a)
                    if m:
                        mpath = m.group(1)
                full = "/" + "/".join(p.strip("/") for p in (class_path, mpath) if p.strip("/"))
                out.append((methods[0], norm_path(full), pending[0][0], "\n".join(ann), "\n".join(class_annots)))
            pending = []
    return out


def norm_path(p):
    p = re.sub(r"\{[^}]+\}", "{}", p)
    p = re.sub(r"/:[A-Za-z_]\w*", "/{}", p)
    p = re.sub(r"\{\{[^}]+\}\}", "{}", p)
    return "/" + "/".join(x for x in p.split("?")[0].split("/") if x)


def bruno_endpoints(repo, rev):
    eps = set()
    for path in ls_tree(repo, rev, "bruno/"):
        if not path.endswith(".bru"):
            continue
        text = show(repo, rev, path) or ""
        m = re.search(r"^(get|post|put|delete|patch|head|options)\s*\{\s*\n\s*url:\s*(\S+)", text, re.M)
        if not m:
            continue
        url = m.group(2).replace("{{baseUrlV1}}", "/v1").replace("{{baseUrl}}", "")
        eps.add((m.group(1).upper(), norm_path(url)))
    return eps


def check_endpoints(repo, files, base, head, f):
    added_eps, removed_eps = [], []
    for d in files.values():
        if not d.path.startswith(MAIN_JAVA) or "/api/" not in d.path or not d.path.endswith(".java"):
            continue
        head_text = show(repo, head, d.path) if d.status != "D" else None
        base_text = show(repo, base, d.old_path or d.path) if d.status != "A" else None
        h = extract_endpoints(head_text)
        b = {(m, p) for m, p, *_ in extract_endpoints(base_text)}
        hs = {(m, p) for m, p, *_ in h}
        for m, p, ln, ann, cls in h:
            if (m, p) not in b:
                added_eps.append((d.path, m, p, ln, ann, cls))
        for m, p in b - hs:
            removed_eps.append((d.path, m, p))
    if not added_eps and not removed_eps:
        return
    bruno = bruno_endpoints(repo, head)
    touched_bruno = any(p.startswith("bruno/") for p in files)
    for path, m, p, ln, ann, cls in added_eps:
        if "/api/sys/" in path:
            continue
        if (m, p) not in bruno:
            f.add("bruno-sync", "should", path, ln,
                  f"New or changed endpoint `{m} /api{p}` has no request in `bruno/`"
                  + ("" if touched_bruno else " and the PR does not touch Bruno") + " (CLAUDE.md: update Bruno in the same change).")
        if not any(a in ann or a in cls for a in SECURITY_ANNOTATIONS):
            f.add("endpoint-security", "blocking", path, ln,
                  f"`{m} /api{p}` has no `@RolesAllowed`/`@PermitAll` on the method or class. "
                  "Quarkus lets unannotated endpoints through without authentication.")
        if "/api/v1/" in path and "@Operation" not in ann:
            f.add("openapi", "should", path, ln, f"v1 endpoint `{m} /api{p}` has no `@Operation`/`@APIResponse`.")
    for path, m, p in removed_eps:
        if (m, p) in bruno:
            f.add("bruno-sync", "should", path, None, f"Endpoint `{m} /api{p}` was removed or changed but its `.bru` request still exists.")


# ---------------------------------------------------------------- env vars / config

def check_env(repo, files, head, f):
    new_vars = {}
    for d in files.values():
        if re.match(r"src/main/resources/application[\w-]*\.properties$", d.path):
            for ln, text in d.added.items():
                if text.lstrip().startswith("#"):
                    continue
                for v in re.findall(r"\$\{([A-Z][A-Z0-9_]+)", text):
                    new_vars.setdefault(v, (d.path, ln))
    if not new_vars:
        return
    example = show(repo, head, ".env.example") or ""
    for v, (path, ln) in new_vars.items():
        if not re.search(rf"^\s*#?\s*{v}\s*=", example, re.M):
            f.add("env-sync", "should", path, ln,
                  f"`${{{v}}}` is new but missing from `.env.example` (and `.env.local` if needed locally).")


def check_appconfig(files, f):
    changed_getters = []
    for d in files.values():
        if d.path.startswith(MAIN_JAVA) and "/config/" in d.path and d.path.endswith(".java"):
            sig = re.compile(r"^\s+[\w<>\[\], ?.]+\s+\w+\(\);\s*$")
            if any(sig.match(t) for t in d.added.values()) or any(sig.match(t) for t in d.removed):
                changed_getters.append(d.path)
    if changed_getters and not any(p.endswith("AppConfigTest.java") for p in files):
        f.add("appconfig-sync", "should", changed_getters[0], None,
              "Config interface getters changed but `AppConfigTest.expectedPropertyPaths()` was not updated; the test will fail.")


# ---------------------------------------------------------------- Java line checks

def java_files(files, prefix):
    return [d for d in files.values() if d.path.startswith(prefix) and d.path.endswith(".java") and d.status != "D"]


def main_class_names(repo, head):
    names = set()
    for p in ls_tree(repo, head, MAIN_JAVA):
        if p.endswith(".java"):
            names.add(p.rsplit("/", 1)[1][:-5])
    return names


def check_tests(repo, files, head, f):
    internal = None
    for d in java_files(files, TEST_JAVA):
        text = show(repo, head, d.path) or ""
        lines = text.splitlines()
        is_resource_test = "io.restassured" in text or "RestAssured" in text
        for ln, t in sorted(d.added.items()):
            s = t.strip()
            mocked = None
            if re.match(r"@(InjectMock|InjectSpy|Mock|Spy)\b", s):
                for nxt in lines[ln:ln + 4]:
                    m = re.match(r"\s*(?:private\s+|protected\s+|public\s+)?(?:final\s+)?([A-Z]\w*)(?:<[^>]*>)?\s+\w+\s*[;=]", nxt)
                    if m:
                        mocked = m.group(1)
                        break
            for m in re.finditer(r"(?:Mockito\.)?\bmock\(\s*([A-Z]\w*)\.class|installMockForType\(\s*\w+\s*,\s*([A-Z]\w*)\.class", s):
                mocked = m.group(1) or m.group(2)
            if mocked and mocked not in MOCK_ALLOWED:
                if internal is None:
                    internal = main_class_names(repo, head)
                if mocked in internal and mocked.endswith("Config"):
                    f.add("test-internal-mock", "nit", d.path, ln,
                          f"Mocks config interface `{mocked}`; prefer a `@TestProfile` or test properties.")
                elif mocked in internal:
                    f.add("test-internal-mock", "should", d.path, ln,
                          f"Mocks internal bean `{mocked}`. CLAUDE.md allows test doubles only for external services "
                          "(in `testdoubles/`) and `AsyncJobScheduler`; use the real bean against the test DB.")
            if s.startswith("@Transactional"):
                window = "\n".join(lines[ln - 1:ln + 4])
                if "@Test" in window or "@ParameterizedTest" in window:
                    if is_resource_test:
                        f.add("test-transactional", "blocking", d.path, ln,
                              "`@Transactional` on a RestAssured test: the HTTP request runs on another thread and cannot see the uncommitted data.")
                    elif "/repository/" not in d.path:
                        f.add("test-transactional", "nit", d.path, ln,
                              "`@Transactional` on a test method is a smell per CLAUDE.md; create data with `TestEntityUtils` instead.")
        if d.status == "A" and "@QuarkusTest" in text:
            touches_db = any(k in text for k in ("TestEntityUtils", "Repository ", "RestAssured", "given()"))
            if touches_db and "reset()" not in text:
                f.add("test-db-reset", "should", d.path, None,
                      "New `@QuarkusTest` touches the DB but never calls `cleaner.reset()` in `@BeforeEach`.")


def layer_of(path):
    rel = path[len(MAIN_JAVA + PKG):] if path.startswith(MAIN_JAVA + PKG) else ""
    return rel.split("/")[0], rel


def check_main_java(repo, files, head, f):
    for d in java_files(files, MAIN_JAVA):
        layer, rel = layer_of(d.path)
        text = None
        comment_run, comment_start = 0, None
        block_start, block_len, block_all_added = None, 0, True
        for ln, t in sorted(d.added.items()):
            s = t.strip()
            # imports / layering
            m = re.match(r"import\s+(static\s+)?([\w.]+)", s)
            if m:
                imp = m.group(2)
                if imp.startswith("javax.") and not imp.startswith(JAVAX_ALLOWED):
                    f.add("javax", "should", d.path, ln, f"`{imp}`: use `jakarta.*` (CLAUDE.md bans `javax.*`).")
                if layer == "api":
                    if ".core.repository." in imp:
                        f.add("layering", "should", d.path, ln, "API layer imports a repository; resources call services only (tenet 1).")
                    if ".core.data.entities." in imp:
                        f.add("layering", "should", d.path, ln, "API layer imports a JPA entity; map service DTOs instead (tenets 1, 9).")
                    if rel.startswith("api/v1/") and ".core.api.v0." in imp and not imp.endswith("ResponseUtil"):
                        f.add("layering", "nit", d.path, ln, "v1 code imports from `api.v0`; v0 is legacy being retired.")
                if layer in ("service", "scheduled", "data") and ".core.api." in imp:
                    f.add("layering", "should", d.path, ln, f"`{layer}` imports the API layer (`{imp.split('.core.')[1]}`); lower layers must not depend on wire DTOs.")
                if layer == "service" and imp in ("jakarta.ws.rs.core.Response", "jakarta.ws.rs.WebApplicationException") \
                        and "/ext/" not in rel and not re.search(r"(Client|Filter)\.java$", d.path):
                    f.add("layering", "should", d.path, ln, "Service imports JAX-RS response types; services return `SuCoMe`, resources build responses.")
                if layer == "repository" and (".core.service." in imp or ".core.api." in imp):
                    f.add("layering", "should", d.path, ln, "Repository depends on a service or the API layer.")
                if rel.startswith("data/entities/") and (".core.service." in imp or ".core.api." in imp):
                    f.add("layering", "should", d.path, ln, "Entity depends on a service or the API layer.")
                continue
            # nested public types
            if re.match(r"^(\s{4,}|\t+)public\s+(static\s+)?(final\s+)?(abstract\s+)?(sealed\s+)?(class|record|enum|interface)\s+\w+", t):
                f.add("nested-type", "should", d.path, ln, "Public nested type; CLAUDE.md tenet 6 wants one file per type.")
            if re.search(r"SuCoMe\.(ok|fail)\(\s*\"", s):
                f.add("sucome-string-code", "nit", d.path, ln, "`SuCoMe` with a string code; use the domain `*ResultCode` enum (tenet 7).")
            if re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", s):
                f.add("empty-catch", "should", d.path, ln, "Empty catch block swallows the error; log it or handle it.")
            if "printStackTrace()" in s or re.search(r"System\.(out|err)\.print", s):
                f.add("stdout", "should", d.path, ln, "Use `LOG` (jboss Logger) instead of stdout / `printStackTrace`.")
            if s.startswith("@Scheduled") and layer == "service":
                f.add("scheduled-location", "nit", d.path, ln, "`@Scheduled` in `service/`; schedulers live in `scheduled/`.")
            if s.startswith("@ObservesAsync") or "@ObservesAsync" in s:
                if text is None:
                    text = show(repo, head, d.path) or ""
                body = method_body(text, ln)
                if body is not None and "catch" not in body:
                    f.add("listener-catch", "should", d.path, ln,
                          "`@ObservesAsync` listener without try/catch; listeners must catch and log (tenet 5). Ignore if it delegates to a method that catches.")
            # comment length (only comment blocks written entirely in this PR)
            if s.startswith("/*"):
                block_start, block_len, block_all_added = ln, 0, True
            if block_start is not None:
                block_len += 1
                if "*/" in s:
                    content = block_len - 2 if block_len > 2 else block_len
                    if content > 5 and block_all_added:
                        f.add("comment-length", "nit", d.path, block_start,
                              f"New comment block has {content} lines; CLAUDE.md hard limit is 5.")
                    block_start = None
                elif ln + 1 not in d.added:
                    block_start = None
            if s.startswith("//"):
                comment_run = comment_run + 1 if comment_start is not None and ln - 1 in d.added and d.added[ln - 1].strip().startswith("//") else 1
                if comment_run == 1:
                    comment_start = ln
                if comment_run == 6:
                    f.add("comment-length", "nit", d.path, comment_start, "New `//` comment run longer than 5 lines.")
            else:
                comment_run, comment_start = 0, None


def collapse_nits(f, limit=3):
    """A nit check that fires on many lines becomes one finding: humans rarely flag these, a list of
    dozens is noise that buries the real findings. Long comments collapse from 2 hits on."""
    by = {}
    for x in f.items:
        if x["severity"] == "nit":
            by.setdefault(x["check"], []).append(x)
    for check, hits in by.items():
        if len(hits) <= (1 if check == "comment-length" else limit):
            continue
        f.items = [x for x in f.items if not (x["severity"] == "nit" and x["check"] == check)]
        where = ", ".join(f"{h['path'].rsplit('/', 1)[1]}:{h['line']}" for h in hits[:6])
        more = f" and {len(hits) - 6} more" if len(hits) > 6 else ""
        f.add(check, "nit", hits[0]["path"], hits[0]["line"],
              f"{len(hits)} occurrences: {where}{more}. First: {hits[0]['message']}")


def method_body(text, decl_line):
    lines = text.splitlines()
    start = decl_line - 1
    joined = "\n".join(lines[start:start + 400])
    i = joined.find("{")
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(joined)):
        c = joined[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return joined[i:j + 1]
    return None


# ---------------------------------------------------------------- main

def run(repo, base, head, develop, open_migrations):
    files = parse_diff(repo, base, head)
    f = Findings()
    check_flyway(repo, files, head, develop or base, open_migrations, f)
    check_endpoints(repo, files, base, head, f)
    check_env(repo, files, head, f)
    check_appconfig(files, f)
    check_tests(repo, files, head, f)
    check_main_java(repo, files, head, f)
    collapse_nits(f)
    order = {"blocking": 0, "should": 1, "nit": 2}
    f.items.sort(key=lambda x: (order[x["severity"]], x["path"], x["line"] or 0))
    return {"base": base, "head": head, "develop": develop, "files_changed": len(files), "findings": f.items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--develop")
    ap.add_argument("--open-migrations")
    ap.add_argument("--out")
    a = ap.parse_args()
    om = json.load(open(a.open_migrations)) if a.open_migrations else []
    res = run(a.repo, a.base, a.head, a.develop, om)
    out = json.dumps(res, indent=2, ensure_ascii=False)
    if a.out:
        open(a.out, "w").write(out)
    else:
        print(out)
    sys.exit(0)


if __name__ == "__main__":
    main()
