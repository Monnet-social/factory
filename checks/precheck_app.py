#!/usr/bin/env python3
"""Deterministic pre-checks for a monnett-app (Flutter) pull request.

Looks only at what the PR adds or changes (diff base..head), never at legacy code. No Flutter or
Dart SDK: plain text, a small Dart scanner, regex and JSON parsing.
Output: JSON {findings: [{check, severity, path, line, message}], info: [...]}.
Severity: blocking | should | nit | question. `info` items are context for the reviewer, never posted.

Usage:
  precheck_app.py --repo <app git dir> --base <sha> --head <sha>
                  [--develop <ref>] [--open-migrations <json>] [--out <file>]
--develop:          state of the target branch (ARB drift already there is not the PR's).
--open-migrations:  accepted for interface parity with precheck.py (core), ignored.
"""
import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

PKG = "monnet"
ARB_DIR = "lib/l10n/"
LOCALES = ("en", "de", "fr")
TEMPLATE = f"{ARB_DIR}intl_en.arb"
LEGACY_DIRS = ("lib/common/", "lib/services/", "lib/controllers/", "lib/views/", "lib/cubits/", "lib/providers/")
TARGET_DIRS = ("lib/core/", "lib/data/", "lib/domain/", "lib/presentation/")
ROUTE_FILES = ("lib/core/constants/app_routes.dart", "lib/core/router/app_router.dart")
FLAGS_FILE = "lib/core/config/feature_flags.dart"
APPENV_ALLOWED = ("lib/core/config/", "app_endpoints.dart", "firebase.dart", "revenuecat_config.dart")
DTO_DIR = "lib/data/data_sources/dtos/"
LOGIC_DIRS = ("lib/presentation/bloc/", "lib/domain/", "lib/data/", "lib/core/util/", "lib/core/validators/")
GENERATED_L10N = re.compile(r"^lib/l10n/app_localizations.*\.dart$")
SDK_DEPS = {"flutter", "flutter_localizations", "flutter_test", "flutter_driver", "integration_test"}
MAX_SITES = 5


# ---------------------------------------------------------------- git helpers

def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def show(repo, rev, path):
    r = subprocess.run(["git", "-C", repo, "show", f"{rev}:{path}"], capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


@dataclass
class FileDiff:
    path: str
    status: str                      # A, M, D, R, C, T
    old_path: str = None
    added: dict = field(default_factory=dict)    # new line number -> text
    removed: list = field(default_factory=list)  # removed texts


def parse_diff(repo, base, head):
    files = {}
    for line in git(repo, "diff", "--name-status", "-M", base, head).splitlines():
        parts = line.split("\t")
        st = parts[0][0]
        if st in ("R", "C") and len(parts) >= 3:
            files[parts[2]] = FileDiff(parts[2], st, old_path=parts[1])
        elif len(parts) >= 2:
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
            new_ln = int(m.group(1)) if m else 0
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
        self.info = []

    def add(self, check, severity, path, line, message):
        self.items.append({"check": check, "severity": severity, "path": path,
                           "line": line, "message": message})

    def note(self, check, path, line, message):
        self.info.append({"check": check, "path": path, "line": line, "message": message})


def site(h):
    return f"{h['path'].rsplit('/', 1)[-1]}:{h['line']}" if h["line"] else h["path"].rsplit("/", 1)[-1]


def collapse(f):
    """Nits: one finding per check per PR. Other severities: at most MAX_SITES findings per check, the
    last one names the rest. A list of dozens buries the findings that matter."""
    by = {}
    for x in f.items:
        by.setdefault((x["check"], x["severity"]), []).append(x)
    out = []
    for (check, sev), hits in by.items():
        if sev == "nit" and len(hits) > 1:
            where = ", ".join(site(h) for h in hits[:MAX_SITES])
            more = f" and {len(hits) - MAX_SITES} more" if len(hits) > MAX_SITES else ""
            out.append({**hits[0], "message": f"{len(hits)} occurrences: {where}{more}. First: {hits[0]['message']}"})
        elif len(hits) > MAX_SITES:
            rest = hits[MAX_SITES:]
            where = ", ".join(site(h) for h in rest[:8])
            more = f" and {len(rest) - 8} more" if len(rest) > 8 else ""
            last = hits[MAX_SITES - 1]
            out += hits[:MAX_SITES - 1]
            out.append({**last, "message": f"{last['message']} Same check also fires at: {where}{more}."})
        else:
            out += hits
    f.items = out


# ---------------------------------------------------------------- Dart scanner

def _blank(chars, i, j):
    for k in range(i, j):
        if chars[k] != "\n":
            chars[k] = " "


def _ident(c):
    return c.isalnum() or c in "_$"


def _skip_string(t, i):
    """t[i] is the opening quote (after an optional r prefix). Returns the index after the closing quote."""
    raw = i > 0 and t[i - 1] == "r" and not (i > 1 and _ident(t[i - 2]))
    q = t[i]
    triple = t.startswith(q * 3, i)
    j = i + (3 if triple else 1)
    n = len(t)
    while j < n:
        c = t[j]
        if not raw and c == "\\":
            j += 2
            continue
        if not raw and c == "$" and j + 1 < n and t[j + 1] == "{":
            j = _skip_block(t, j + 1)
            continue
        if triple and t.startswith(q * 3, j):
            return j + 3
        if not triple and c == q:
            return j + 1
        if not triple and c == "\n":   # unterminated: stop at the line end
            return j
        j += 1
    return n


def _skip_block(t, i):
    """t[i] == '{' (string interpolation). Returns the index after the matching '}'."""
    depth, j, n = 0, i, len(t)
    while j < n:
        c = t[j]
        if t.startswith("//", j):
            k = t.find("\n", j)
            j = n if k < 0 else k
            continue
        if t.startswith("/*", j):
            j = _skip_comment(t, j)
            continue
        if c in "'\"":
            j = _skip_string(t, j)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def _skip_comment(t, i):
    depth, j, n = 0, i, len(t)   # Dart block comments nest
    while j < n:
        if t.startswith("/*", j):
            depth += 1
            j += 2
        elif t.startswith("*/", j):
            depth -= 1
            j += 2
            if depth == 0:
                return j
        else:
            j += 1
    return n


def mask_dart(t):
    """Returns (code, nocomment): same length and line structure as t. `code` has comments and string
    contents blanked (quotes kept); `nocomment` has only comments blanked."""
    code, noc = list(t), list(t)
    i, n = 0, len(t)
    while i < n:
        if t.startswith("//", i):
            j = t.find("\n", i)
            j = n if j < 0 else j
            _blank(code, i, j)
            _blank(noc, i, j)
            i = j
        elif t.startswith("/*", i):
            j = _skip_comment(t, i)
            _blank(code, i, j)
            _blank(noc, i, j)
            i = j
        elif t[i] in "'\"":
            j = _skip_string(t, i)
            q = 3 if t.startswith(t[i] * 3, i) else 1
            _blank(code, i + q, max(i + q, j - q))
            i = j
        else:
            i += 1
    return "".join(code), "".join(noc)


def line_starts(t):
    starts = [0]
    for m in re.finditer("\n", t):
        starts.append(m.end())
    return starts


def line_at(starts, pos):
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def brace_pairs(code):
    """open index -> close index for every {} block in masked code."""
    stack, pairs = [], {}
    for i, c in enumerate(code):
        if c == "{":
            stack.append(i)
        elif c == "}" and stack:
            pairs[stack.pop()] = i
    for o in stack:
        pairs[o] = len(code)
    return pairs


def enclosing(pairs, pos):
    """Blocks containing pos, innermost first: list of (open, close)."""
    return sorted(((o, c) for o, c in pairs.items() if o < pos <= c), key=lambda b: -b[0])


class DartFile:
    def __init__(self, text):
        self.text = text
        self.code, self.noc = mask_dart(text)
        self.code_lines = self.code.splitlines()
        self.noc_lines = self.noc.splitlines()
        self.starts = line_starts(text)
        self._pairs = None

    @property
    def pairs(self):
        if self._pairs is None:
            self._pairs = brace_pairs(self.code)
        return self._pairs

    def cline(self, ln):
        return self.code_lines[ln - 1] if 0 < ln <= len(self.code_lines) else ""

    def nline(self, ln):
        return self.noc_lines[ln - 1] if 0 < ln <= len(self.noc_lines) else ""

    def ln(self, pos):
        return line_at(self.starts, pos)


# ---------------------------------------------------------------- ARB / ICU

def parse_icu(msg):
    """Placeholder names of an ICU message in order of first appearance, the names used as
    plural/select selectors, and the names used outside any branch. Returns (names, selectors,
    top_level) or None if the message does not parse."""
    names, selectors, top = [], set(), set()

    def message(i, stop_at_brace):
        while i < len(msg):
            c = msg[i]
            if c == "{":
                if not stop_at_brace:
                    m = re.compile(r"\{\s*([A-Za-z_]\w*)").match(msg, i)
                    if m:
                        top.add(m.group(1))
                i = argument(i)
            elif c == "}":
                if stop_at_brace:
                    return i
                raise ValueError("unbalanced }")
            else:
                i += 1
        if stop_at_brace:
            raise ValueError("unterminated branch")
        return i

    def argument(i):
        m = re.compile(r"\{\s*([A-Za-z_]\w*)\s*([,}])").match(msg, i)
        if not m:
            raise ValueError("bad argument")
        name = m.group(1)
        if name not in names:
            names.append(name)
        if m.group(2) == "}":
            return m.end()
        m2 = re.compile(r"\s*(\w+)\s*(,|})").match(msg, m.end())
        if not m2:
            raise ValueError("bad type")
        if m2.group(2) == "}":   # {date, date}
            return m2.end()
        if m2.group(1) in ("plural", "select", "selectordinal"):
            selectors.add(name)
        i = m2.end()
        while True:
            m3 = re.compile(r"\s*(=?[\w-]+)\s*\{").match(msg, i)
            if not m3:
                m4 = re.compile(r"\s*\}").match(msg, i)
                if not m4:
                    raise ValueError("bad branch")
                return m4.end()
            j = message(m3.end(), True)
            i = j + 1

    try:
        message(0, False)
    except ValueError:
        return None
    return names, selectors, top


def icu_names(msg):
    r = parse_icu(msg)
    if r is not None:
        return r[0]
    return list(dict.fromkeys(re.findall(r"\{\s*([A-Za-z_]\w*)\s*[,}]", msg)))


class Pairs(list):
    """A JSON object as its (key, value) pairs: keeps duplicate keys and order."""


def as_dict(v):
    if isinstance(v, Pairs):
        return {k: as_dict(x) for k, x in v}
    if isinstance(v, list):
        return [as_dict(x) for x in v]
    return v


def load_arb(text):
    """(pairs, None) or (None, error). Top-level pairs keep duplicates and order; values are plain."""
    if text is None:
        return None, "missing"
    try:
        pairs = json.loads(text, object_pairs_hook=Pairs)
    except ValueError as e:
        return None, str(e)
    if not isinstance(pairs, Pairs):
        return None, "not an object"
    return [(k, as_dict(v)) for k, v in pairs], None


def arb_messages(pairs):
    return {k: v for k, v in pairs if not k.startswith("@") and isinstance(v, str)}


def arb_meta(pairs):
    return {k[1:]: v for k, v in pairs if k.startswith("@") and not k.startswith("@@") and isinstance(v, dict)}


def key_line(text, key):
    m = re.search(r'^\s*"' + re.escape(key) + r'"\s*:', text or "", re.M)
    return (text.count("\n", 0, m.start()) + 1) if m else None


def param_order(msg, meta):
    """Parameter order of the generated method: declared placeholders in block order, inferred ones
    (no metadata) alphabetically after them."""
    declared = list(((meta or {}).get("placeholders") or {}).keys())
    inferred = sorted(n for n in icu_names(msg) if n not in declared)
    return declared + inferred


def check_arb(repo, files, base, head, develop, added_dart, f):
    touched = [p for p in files if p.startswith(ARB_DIR) and p.endswith(".arb")]
    if not touched:
        return
    head_txt = {l: show(repo, head, f"{ARB_DIR}intl_{l}.arb") for l in LOCALES}
    base_txt = {l: show(repo, base, f"{ARB_DIR}intl_{l}.arb") for l in LOCALES}
    dev_txt = {l: show(repo, develop, f"{ARB_DIR}intl_{l}.arb") for l in LOCALES} if develop else base_txt
    head_p, base_p, dev_p = {}, {}, {}
    for l in LOCALES:
        path = f"{ARB_DIR}intl_{l}.arb"
        pairs, err = load_arb(head_txt[l])
        if pairs is None:
            if head_txt[l] is not None:
                f.add("arb-duplicate-key", "blocking", path, None, f"`intl_{l}.arb` is not valid JSON at the PR head ({err}); `gen-l10n` fails.")
            continue
        head_p[l] = pairs
        base_p[l] = load_arb(base_txt[l])[0] or []
        dev_p[l] = load_arb(dev_txt[l])[0] or []
        # 2. duplicates the PR introduced
        seen, dups = set(), []
        for k, _ in pairs:
            if k in seen:
                dups.append(k)
            seen.add(k)
        old_dups = {k for k, n in _counts(base_p[l]).items() if n > 1}
        new_dups = [k for k in dict.fromkeys(dups) if k not in old_dups]
        if new_dups:
            f.add("arb-duplicate-key", "should", path, key_line(head_txt[l], new_dups[0]),
                  f"Duplicate key(s) in `intl_{l}.arb`: {', '.join(f'`{k}`' for k in new_dups[:8])}. "
                  "JSON parsing and `gen-l10n` silently keep the last value.")
    if "en" not in head_p:
        return
    msgs = {l: arb_messages(p) for l, p in head_p.items()}
    base_msgs = {l: arb_messages(p) for l, p in base_p.items()}
    dev_msgs = {l: arb_messages(p) for l, p in dev_p.items()}
    meta_en, base_meta_en = arb_meta(head_p["en"]), arb_meta(base_p.get("en", []))

    # 1. key parity at head, minus drift already on the target branch
    en_keys = set(msgs["en"])
    for l in ("de", "fr"):
        if l not in msgs:
            continue
        path = f"{ARB_DIR}intl_{l}.arb"
        dev_missing = set(dev_msgs.get("en", {})) - set(dev_msgs.get(l, {}))
        dev_extra = set(dev_msgs.get(l, {})) - set(dev_msgs.get("en", {}))
        missing = sorted(en_keys - set(msgs[l]) - dev_missing)
        extra = sorted(set(msgs[l]) - en_keys - dev_extra)
        if missing:
            f.add("arb-parity", "should", path, None,
                  f"{len(missing)} key(s) in `intl_en.arb` missing from `intl_{l}.arb`: "
                  + ", ".join(f"`{k}`" for k in missing[:8]) + (" …" if len(missing) > 8 else "")
                  + ". gen-l10n falls back to English for them.")
        if extra:
            f.add("arb-parity", "should", path, key_line(head_txt[l], extra[0]),
                  f"{len(extra)} key(s) in `intl_{l}.arb` not in the template `intl_en.arb`: "
                  + ", ".join(f"`{k}`" for k in extra[:8]) + (" …" if len(extra) > 8 else "")
                  + ". Orphans (removed from en, left here) are dead text.")

    changed = {l: {k for k, v in msgs[l].items() if base_msgs.get(l, {}).get(k) != v} for l in msgs}
    new_en = {k for k in msgs["en"] if k not in base_msgs.get("en", {})}

    # 3. generated parameter order changed for an existing key with the same placeholder set
    for k, v in msgs["en"].items():
        old = base_msgs.get("en", {}).get(k)
        if old is None:
            continue
        new_order = param_order(v, meta_en.get(k))
        old_order = param_order(old, base_meta_en.get(k))
        if len(new_order) < 2 or set(new_order) != set(old_order) or new_order == old_order:
            continue
        called = any(re.search(r"\." + re.escape(k) + r"\s*\(", t) for t in added_dart)
        sev = "nit" if called else "blocking"
        f.add("arb-placeholder-order", sev, TEMPLATE, key_line(head_txt["en"], k),
              f"`AppLocalizations.{k}` parameters change from ({', '.join(old_order)}) to ({', '.join(new_order)}) "
              "but keep the same names, so existing positional call sites still compile and render swapped values. "
              + ("Verify the argument order at every call site." if called
                 else "No call site is updated in this PR: fix the callers or keep the old order in the `@` block."))

    # 4. metadata on added/changed template keys
    order = [k for k, _ in head_p["en"]]
    for k in sorted(changed.get("en", set())):
        v = msgs["en"][k]
        ln = key_line(head_txt["en"], k)
        r = parse_icu(v)
        names = icu_names(v)
        meta = meta_en.get(k)
        ph = (meta or {}).get("placeholders") if meta else None
        if names and not ph:
            f.add("arb-metadata", "should", TEMPLATE, ln,
                  f"`{k}` has placeholders ({', '.join(names)}) but no `@{k}` block with `placeholders` (lib/l10n/README.md).")
        if meta is not None and f"@{k}" in order and order.index(f"@{k}") != order.index(k) + 1:
            f.add("arb-metadata", "nit", TEMPLATE, ln, f"`@{k}` is not directly after `{k}`.")
        if k in new_en and not (meta or {}).get("description"):
            # README asks for it, practice mostly skips it (101 of 957 keys have one): context, not a finding
            f.note("arb-description", TEMPLATE, ln, f"New key `{k}` has no `description` in its `@` block.")
        if ph and r is not None:
            for name, spec in ph.items():
                if isinstance(spec, dict) and spec.get("type") == "int" and name not in r[1] and name in r[2] \
                        and not re.search(r"\{\s*" + re.escape(name) + r"\s*\}%", v):
                    f.add("arb-metadata", "nit", TEMPLATE, ln,
                          f"`{k}`: `int` placeholder `{name}` is not used as an ICU plural/select (README: int placeholders must).")

    # 5. placeholder names differ between en and a translation (for keys the PR touches)
    for l in ("de", "fr"):
        if l not in msgs:
            continue
        for k in sorted(changed.get(l, set()) | changed.get("en", set())):
            if k not in msgs[l] or k not in msgs["en"]:
                continue
            a, b = set(icu_names(msgs["en"][k])), set(icu_names(msgs[l][k]))
            if b - a:   # a translation may omit a value (word order), but cannot invent a name
                f.add("arb-locale-placeholders", "should", f"{ARB_DIR}intl_{l}.arb", key_line(head_txt[l], k),
                      f"`{k}`: placeholder(s) {sorted(b - a)} in `intl_{l}.arb` are not in the en template {sorted(a)}; "
                      "a translated or invented name is not passed by the generated method.")

    # 18. new template keys without a Dart reference
    if new_en:
        used = set()
        keys = sorted(new_en)
        for i in range(0, len(keys), 200):
            args = []
            for k in keys[i:i + 200]:
                args += ["-e", k]
            out = git(repo, "grep", "-o", "-h", "-w", *args, head, "--", "lib/*.dart",
                      ":(exclude)lib/l10n/app_localizations*.dart", check=False)
            used |= {l.split(":")[-1].strip() for l in out.splitlines()}
        for k in keys:
            if k not in used:
                f.add("arb-dead-key", "nit", TEMPLATE, key_line(head_txt["en"], k),
                      f"New key `{k}` has no Dart reference yet (dead text, or a consumer in a later PR).")


def _counts(pairs):
    out = {}
    for k, _ in pairs:
        out[k] = out.get(k, 0) + 1
    return out


# ---------------------------------------------------------------- imports / layering

IMPORT = re.compile(r"""^\s*(?:import|export)\s+['"]([^'"]+)['"]""")


def resolve_import(src_path, uri):
    if uri.startswith(f"package:{PKG}/"):
        return "lib/" + uri[len(f"package:{PKG}/"):]
    if uri.startswith(("package:", "dart:")):
        return None
    parts = src_path.split("/")[:-1]
    for seg in uri.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg != ".":
            parts.append(seg)
    return "/".join(parts)


def check_imports(files, f):
    for d in dart_files(files, "lib/"):
        is_new = d.status == "A" or (d.status in ("R", "C") and d.old_path and not d.old_path.startswith(LEGACY_DIRS)
                                     and d.path.startswith(LEGACY_DIRS))
        if is_new and d.path.startswith(LEGACY_DIRS):
            f.add("layer-import", "should", d.path, None,
                  f"New file in legacy `{d.path.split('/')[1]}/`; CLAUDE.md: new code goes to core/ data/ domain/ presentation/.")
        for ln, t in sorted(d.added.items()):
            m = IMPORT.match(t)
            if not m:
                continue
            target = resolve_import(d.path, m.group(1))
            if not target:
                continue
            if d.path.startswith("lib/domain/") and target.startswith("lib/presentation/"):
                f.add("layer-import", "should", d.path, ln, "`domain/` imports `presentation/`; the domain layer must not depend on UI.")
            elif d.path.startswith("lib/data/") and target.startswith("lib/presentation/"):
                f.add("layer-import", "should", d.path, ln, "`data/` imports `presentation/`; data depends on core/ (and domain entities) only.")
            if d.status == "A" and d.path.startswith(TARGET_DIRS) and target.startswith(LEGACY_DIRS):
                f.note("legacy-import-in-new-file", d.path, ln, f"New target-tree file imports legacy `{target}`.")
            if d.status == "A" and d.path.startswith("lib/core/") and target.startswith("lib/presentation/"):
                f.note("core-imports-presentation", d.path, ln, f"New `core/` file imports `{target}`.")


def dart_files(files, prefix):
    return [d for d in files.values() if d.path.startswith(prefix) and d.path.endswith(".dart")
            and d.status != "D" and not GENERATED_L10N.match(d.path)]


# ---------------------------------------------------------------- line checks on Dart code

ROUTE_CALL = re.compile(r"""\.(?:push|go|pushReplacement|replace|pushNamed|goNamed)\s*(?:<[^>]*>)?\(\s*['"]/""")
ROUTE_PATH = re.compile(r"""\bpath\s*:\s*['"]/?\w""")
APPENV = re.compile(r"\bAppEnv\.is(?:Prod|Dev|Local)\b")
UI_STRING = re.compile(
    r"""(?:\bText\s*\(\s*|\bTextSpan\s*\(\s*text\s*:\s*|\b(?:title|label|hintText|labelText|tooltip|semanticLabel|"""
    r"""semanticsLabel|message|helperText|errorText|content)\s*:\s*|SnackbarHelper\.show\w*\(\s*context\s*,\s*|"""
    r"""\bshowSnackbar\s*\(\s*)(['"])((?:\\.|(?!\1).)*)\1""")
UI_HEAD = re.compile(r"\b(?:Text|TextSpan|SnackBar|AlertDialog|HelperService\.show\w*|SnackbarHelper\.show\w*|showSnackbar)\s*\(")
LONE_LITERAL = re.compile(r"""^\s*(['"])((?:\\.|(?!\1).)*)\1\s*[,)]*\s*;?\s*$""")
UI_EXEMPT = re.compile(r"\b(Key|ValueKey|debugLabel|heroTag|restorationId|name)\b\s*[(:]|assets/|\.(png|svg|jpg|json|webp)\b")
IGNORE_HARDCODED = re.compile(r"//\s*ignore\s*:\s*([\w/,\s]*hardcoded_string)(.*)$")
TODO = re.compile(r"//.*\bTODO\b")


def has_words(literal):
    """Looks like text a user reads, not a key, tag, id or path: two words, or one capitalized word."""
    s = re.sub(r"\$\{[^}]*\}?|\$\w+|\\.", " ", literal).strip()
    if not s or re.search(r"[/\\]|\.(png|svg|jpg|json|webp|dart)\b", s):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", s)
    if not words or re.fullmatch(r"[\w.\-:#]+", s) and not re.fullmatch(r"[A-ZÀ-Þ][a-zß-ÿ]+", s):
        return False   # slug / identifier / key like `post-`, `convo_rows_group`
    return len(words) >= 2 or bool(re.match(r"[A-ZÀ-Þ][a-zß-ÿ]", s))


def check_dart_lines(repo, files, head, cache, f):
    for d in dart_files(files, "lib/"):
        if not d.added:
            continue
        df = cache(d.path)
        if df is None:
            continue
        is_route_file = d.path in ROUTE_FILES
        for ln, raw in sorted(d.added.items()):
            code, noc = df.cline(ln), df.nline(ln)
            # 7. route literals
            if not is_route_file and (ROUTE_CALL.search(noc) or (ROUTE_PATH.search(noc) and "GoRoute" in "".join(
                    df.cline(i) for i in range(max(1, ln - 3), ln + 1)))):
                f.add("route-literal", "should", d.path, ln, "Route path as a string literal; use the `AppRoutes` constants (CLAUDE.md \"Routing\").")
            # 8. print / developer.log
            if re.search(r"(?<![\w.])print\s*\(", code):
                f.add("print-log", "should", d.path, ln, "`print(` in app code; use `debugPrint` (release-silent) or the logger.")
            if re.search(r"\bdeveloper\.log\s*\(", code):
                f.add("print-log", "nit", d.path, ln, "`developer.log(` added; per-item logging in parsing or hot paths is noise.")
            # 9. AppEnv gating outside config
            if APPENV.search(code) and not any(a in d.path for a in APPENV_ALLOWED):
                f.add("flag-env", "should", d.path, ln,
                      "Gating on `AppEnv` outside config; unreleased work resolves to `kDebugMode` via `FeatureFlags` (CLAUDE.md \"Feature flags\").")
            # 11. ignore of hardcoded_string
            if "ignore_for_file" in raw and "hardcoded_string" in raw:
                f.add("ignore-hardcoded-string", "should", d.path, ln,
                      "`ignore_for_file` for `hardcoded_string` silences the l10n lint for the whole file; localize instead.")
            else:
                m = IGNORE_HARDCODED.search(raw)
                nxt = "l10n-ignore-next-line" in raw
                if m or nxt:
                    reason = m.group(2).strip(" -—:–.") if m else raw.split("l10n-ignore-next-line", 1)[1].strip(" -—:–.")
                    before = raw.split("//", 1)[0]
                    target = ln if before.strip() and not nxt else next(
                        (i for i in range(ln + 1, ln + 4) if df.nline(i).strip()), ln + 1)
                    lits = [x.group(2) for x in re.finditer(r"""(['"])((?:\\.|(?!\1).)*)\1""", df.nline(target))]
                    wordy = [x for x in lits if has_words(x)]
                    if wordy or not reason:
                        why = f"silences user-visible text `'{wordy[0][:40]}'`" if wordy else "has no reason after the rule name"
                        f.add("ignore-hardcoded-string", "should" if wordy else "nit", d.path, ln,
                              f"`hardcoded_string` ignore {why}; CLAUDE.md allows it only for symbols/format tokens, with a reason.")
            # 16. hardcoded UI strings
            if d.path.startswith(("lib/presentation/", "lib/common/", "lib/views/")) and not UI_EXEMPT.search(noc):
                prev_raw = d.added.get(ln - 1) or (df.text.splitlines()[ln - 2] if ln > 1 else "")
                silenced = "hardcoded_string" in raw or "hardcoded_string" in prev_raw or "l10n-ignore-next-line" in prev_raw
                lits = [m.group(2) for m in UI_STRING.finditer(noc)]
                lone = LONE_LITERAL.match(noc)
                if lone and any(UI_HEAD.search(df.nline(i)) for i in range(max(1, ln - 4), ln)):
                    lits.append(lone.group(2))
                lit = next((x for x in lits if has_words(x)), None)
                if lit is not None and not silenced:
                    f.add("hardcoded-ui-string", "should", d.path, ln,
                          f"User-visible literal `'{lit[:50]}'`; use `context.l10n` with keys in en/de/fr.")
            # 20. TODO without ticket
            if TODO.search(raw) and not re.search(r"MNT-\d+", raw):
                f.add("todo-ticket", "nit", d.path, ln, "`TODO` without an `MNT-` ticket reference.")


# ---------------------------------------------------------------- feature flags

def check_flags(files, f):
    d = files.get(FLAGS_FILE)
    if not d:
        return
    old = {}
    for t in d.removed:
        m = re.search(r"static\s+const\s+bool\s+(\w+)\s*=\s*([^;]+);", t)
        if m:
            old[m.group(1)] = m.group(2).strip()
    for ln, t in sorted(d.added.items()):
        m = re.search(r"static\s+const\s+bool\s+(\w+)\s*=\s*([^;]+);", t)
        if not m:
            continue
        name, val = m.group(1), m.group(2).strip()
        if name in old and old[name] != val:
            f.add("flag-env", "question", d.path, ln,
                  f"`FeatureFlags.{name}` changes from `{old[name]}` to `{val}`: intended to ship, or a local testing flip?")
        elif name not in old and val != "kDebugMode":
            f.add("flag-env", "question", d.path, ln,
                  f"New flag `{name}` = `{val}`; unreleased work should resolve to `kDebugMode`. Intended?")


# ---------------------------------------------------------------- DTOs

DTO_DEFAULT = re.compile(r"""\?\?\s*(?:''|""|0(?:\.0)?\b|false\b|true\b|const\b|<[^>]*>\s*[\[{]|\[\s*\]|\{\s*\})|"""
                         r"""\bjson\s*\[[^\]]+\]\s*\??\.toString\(\)|\btry\s*\{|\bcatch\s*\(""")


def fromjson_ranges(df):
    out = []
    for m in re.finditer(r"\bfromJson\s*\(", df.code):
        i = df.code.find("{", m.end())
        arrow = df.code.find("=>", m.end())
        semi = df.code.find(";", m.end())
        if arrow >= 0 and (i < 0 or arrow < i) and (semi < 0 or arrow < semi):
            end = df.code.find(";", arrow)
            out.append((df.ln(m.start()), df.ln(end if end >= 0 else arrow)))
        elif i >= 0 and (semi < 0 or i < semi):
            close = df.pairs.get(i, i)
            out.append((df.ln(m.start()), df.ln(close)))
    return out


def check_dtos(repo, files, head, cache, f):
    for d in dart_files(files, "lib/"):
        if not d.added:
            continue
        df = cache(d.path)
        if df is None:
            continue
        in_dto = d.path.startswith(DTO_DIR)
        if not in_dto and d.path.startswith("lib/presentation/"):
            continue  # hydrated cubit state: defaults there are migrations of persisted state, not the wire contract
        ranges = [] if in_dto else fromjson_ranges(df)
        hits = []
        for ln in sorted(d.added):
            if not in_dto and not any(a <= ln <= b for a, b in ranges):
                continue
            if DTO_DEFAULT.search(df.nline(ln)):
                hits.append(ln)
        if hits:
            f.add("dto-default", "question", d.path, hits[0],
                  f"{len(hits)} added line(s) default or swallow JSON values (`?? ''`, `?? 0`, `?? []`, `toString()`, try/catch)"
                  f" at line(s) {', '.join(map(str, hits[:6]))}. CLAUDE.md \"DTO parsing\" is fail-fast: default only fields the "
                  "core contract declares nullable.")
    # 10. new DTO without a test
    for d in files.values():
        if d.status != "A" or not d.path.startswith(DTO_DIR) or not d.path.endswith("_dto.dart"):
            continue
        df = cache(d.path)
        classes = re.findall(r"\bclass\s+(\w+)", df.code) if df else []
        stem = d.path.rsplit("/", 1)[1]
        args = ["-e", stem]
        for c in classes:
            args += ["-e", c]
        hit = git(repo, "grep", "-l", "-w", *args, head, "--", "test/", check=False).strip()
        if not hit:
            f.add("dto-no-test", "should", d.path, None,
                  f"New DTO ({', '.join(classes) or stem}) has no test under `test/`; CLAUDE.md: every DTO ships a `fromJson` test on a real payload.")


# ---------------------------------------------------------------- context after await

CONTEXT_USE = re.compile(
    r"\bcontext\s*\.\s*(?:push|go|pop|read|watch|l10n|replace|pushReplacement|goNamed|pushNamed|canPop|select)\b"
    r"|\b\w+\.of\(\s*context\b|\bshow\w*Dialog\s*(?:<[^>]*>)?\(|\bshowModalBottomSheet\s*(?:<[^>]*>)?\("
    r"|\bSnackbarHelper\.\w+\(\s*context\b")


def statement_end(code, i):
    depth, n = 0, len(code)
    while i < n:
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif c == ";" and depth == 0:
            return i
        i += 1
    return n


def unreachable_after(code, pairs, await_pos, after, body, use):
    """True if the use sits in another branch than the await (an `else` of a block that holds the await),
    or a block that holds the await exits (return/throw) before control reaches the use."""
    for o, c in enclosing(pairs, await_pos):
        if (o, c) == body:
            return False
        if o < use <= c:
            continue
        if re.match(r"\s*else\b", code[c + 1:]):
            return True
        if re.search(r"\b(return|throw|rethrow)\b", code[after:c]):
            return True
    return False


def check_context_after_await(files, cache, f):
    for d in dart_files(files, "lib/"):
        if not d.added:
            continue
        df = cache(d.path)
        if df is None or "await" not in df.code or "context" not in df.code:
            continue
        code, pairs = df.code, df.pairs
        reported = set()
        for m in re.finditer(r"\bawait\b", code):
            pos = m.start()
            body = next(((o, c) for o, c in enclosing(pairs, pos)
                         if re.search(r"\basync\*?\s*$", code[max(0, o - 12):o])), None)
            if body is None:
                continue
            start = statement_end(code, m.end())
            await_ln = df.ln(pos)
            for u in CONTEXT_USE.finditer(code, start, body[1]):
                use_ln = df.ln(u.start())
                if use_ln in reported or (await_ln not in d.added and use_ln not in d.added):
                    continue
                between = code[start:u.start()]
                if re.search(r"\bmounted\b", between):
                    continue
                if re.search(r"\bcase\b[^;{}]*:|\bdefault\s*:", between):
                    continue  # another switch case: Dart 3 cases do not fall through
                if unreachable_after(code, pairs, pos, start, body, u.start()):
                    continue
                # the use belongs to a closure defined after the await that is not async-called here: still a gap
                reported.add(use_ln)
                f.add("context-after-await", "should", d.path, use_ln,
                      f"`{u.group(0).strip()}` after `await` (line {await_ln}) with no `mounted`/`context.mounted` check in "
                      "between; the widget may be gone (use_build_context_synchronously is not gated in CI).")
                break


# ---------------------------------------------------------------- controllers without dispose

CTRL_TYPES = r"(?:TextEditingController|AnimationController|ScrollController|PageController|TabController|FocusNode|VideoPlayerController)"
CTRL_ASSIGN = re.compile(r"\b(\w+)\s*=\s*(?:new\s+)?(" + CTRL_TYPES + r")\b(?:\.\w+)?\s*\(")
SUB_FIELD = re.compile(r"\b(StreamSubscription|Timer)\b(?:<[^>]*>)?\s*\??\s+(\w+)\s*[;=]")


def check_dispose(files, cache, f):
    for d in dart_files(files, "lib/"):
        if not d.added:
            continue
        df = cache(d.path)
        if df is None:
            continue
        done = set()
        for ln in sorted(d.added):
            code = df.cline(ln)
            hits = [(m.group(1), m.group(2), "dispose") for m in CTRL_ASSIGN.finditer(code)]
            hits += [(m.group(2), m.group(1), "cancel") for m in SUB_FIELD.finditer(code)]
            for name, typ, verb in hits:
                if name in done:
                    continue
                done.add(name)
                if re.search(r"\b" + re.escape(name) + r"\s*[?!]?\s*\.\s*(?:" + verb + r"|dispose|cancel)\s*\(", df.code):
                    continue
                # handed to something that owns it (a list disposed in a loop, a parent, a return value)
                if re.search(r"\breturn\s+" + re.escape(name) + r"\b|\[\s*[^\]]*\b" + re.escape(name) + r"\b[^\]]*\]", df.code):
                    continue
                pos = df.starts[ln - 1] + code.find(name)
                blocks = enclosing(df.pairs, pos)
                is_local = bool(blocks) and not re.search(r"\b(class|mixin|extension)\b[^{;]*$",
                                                          df.code[max(0, blocks[0][0] - 200):blocks[0][0]])
                sev = "nit" if (is_local and typ == "FocusNode") else "should"
                f.add("controller-no-dispose", sev, d.path, ln,
                      f"`{name}` ({typ}) is created but never `.{verb}()`ed in this file; it leaks listeners/native resources.")


# ---------------------------------------------------------------- pubspec

def pubspec_sections(text):
    """line number -> (section, dep name or None, indent) for lines of a pubspec."""
    out, section, dep = {}, None, None
    for i, line in enumerate((text or "").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            out[i] = (section, dep, None)
            continue
        indent = len(line) - len(line.lstrip())
        m = re.match(r"(\s*)([\w-]+)\s*:", line)
        if indent == 0:
            section, dep = (m.group(2) if m else None), None
        elif indent == 2 and m:
            dep = m.group(2)
        out[i] = (section, dep, indent)
    return out


def deps_of(text):
    deps = {}
    for i, (section, dep, indent) in pubspec_sections(text).items():
        if section in ("dependencies", "dev_dependencies", "dependency_overrides") and dep and indent == 2:
            deps.setdefault(section, set()).add(dep)
    return deps


def check_pubspec(repo, files, base, head, f):
    d = files.get("pubspec.yaml")
    if not d or d.status == "D":
        return
    head_txt = show(repo, head, "pubspec.yaml") or ""
    base_deps = deps_of(show(repo, base, "pubspec.yaml") or "")
    secs = pubspec_sections(head_txt)
    lines = head_txt.splitlines()
    reported = set()
    for ln, t in sorted(d.added.items()):
        section, dep, indent = secs.get(ln, (None, None, None))
        if section not in ("dependencies", "dev_dependencies", "dependency_overrides") or not dep:
            continue
        if section == "dependency_overrides" and dep not in base_deps.get("dependency_overrides", set()) and ("o", dep) not in reported:
            reported.add(("o", dep))
            f.add("pubspec-deps", "question", "pubspec.yaml", ln,
                  f"New `dependency_overrides` entry `{dep}`: overrides apply to the whole graph; is it temporary, with a ticket to remove it?")
            continue
        m = re.match(r"\s{2}[\w-]+\s*:\s*['\"]?any['\"]?\s*$", t)
        if m and dep not in reported:
            reported.add(dep)
            f.add("pubspec-deps", "question", "pubspec.yaml", ln,
                  f"`{dep}: any` is unbounded and `pubspec.lock` is gitignored, so every build may resolve another version.")
        if re.match(r"\s{4}(git|path)\s*:", t) and dep not in reported:
            reported.add(dep)
            f.add("pubspec-deps", "question", "pubspec.yaml", ln,
                  f"`{dep}` comes from a `{t.strip().split(':')[0]}:` source; pin a ref/tag and note why the hosted package is not used.")
    new = deps_of(head_txt).get("dependencies", set()) - base_deps.get("dependencies", set()) - SDK_DEPS
    for dep in sorted(new):
        if dep in reported:
            continue
        used = git(repo, "grep", "-l", "-F", f"package:{dep}/", head, "--", "lib/", check=False).strip()
        if not used:
            ln = next((i for i, l in enumerate(lines, 1) if re.match(r"\s{2}" + re.escape(dep) + r"\s*:", l)), None)
            f.add("pubspec-deps", "nit", "pubspec.yaml", ln, f"New dependency `{dep}` is not imported anywhere in `lib/`.")


# ---------------------------------------------------------------- tests missing

def check_tests_missing(files, cache, f):
    if any(p.startswith("test/") for p in files):
        return
    total, first = 0, None
    for d in dart_files(files, "lib/"):
        if not d.path.startswith(LOGIC_DIRS):
            continue
        df = cache(d.path)
        for ln in d.added:
            s = (df.cline(ln) if df else d.added[ln]).strip()
            if s and not IMPORT.match(d.added[ln]) and s not in ("}", "{", ");", "],", "),"):
                total += 1
                first = first or d.path
    if total > 20:
        f.add("tests-missing", "question", first, None,
              f"{total} added logic lines in cubits/domain/data/util and no change under `test/`. Which test covers the new behaviour?")


# ---------------------------------------------------------------- main

def run(repo, base, head, develop):
    files = parse_diff(repo, base, head)
    f = Findings()
    texts = {}

    def cache(path):
        if path not in texts:
            t = show(repo, head, path)
            texts[path] = DartFile(t) if t is not None and "\0" not in t[:2000] else None
        return texts[path]

    added_dart = [t for d in dart_files(files, "lib/") for t in d.added.values()]
    check_arb(repo, files, base, head, develop, added_dart, f)
    check_imports(files, f)
    check_dart_lines(repo, files, head, cache, f)
    check_flags(files, f)
    check_dtos(repo, files, head, cache, f)
    check_context_after_await(files, cache, f)
    check_dispose(files, cache, f)
    check_pubspec(repo, files, base, head, f)
    check_tests_missing(files, cache, f)
    collapse(f)
    notes = [x for x in f.info if x["check"] == "arb-description"]
    if len(notes) > 1:
        keys = [re.search(r"`([^`]+)`", x["message"]).group(1) for x in notes]
        f.info = [x for x in f.info if x["check"] != "arb-description"] + [
            {**notes[0], "message": f"{len(keys)} new keys have no `description`: " + ", ".join(keys[:10]) + (" …" if len(keys) > 10 else "")}]
    order = {"blocking": 0, "should": 1, "question": 2, "nit": 3}
    f.items.sort(key=lambda x: (order[x["severity"]], x["path"] or "", x["line"] or 0))
    return {"base": base, "head": head, "develop": develop, "files_changed": len(files),
            "findings": f.items, "info": f.info}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--develop")
    ap.add_argument("--open-migrations")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = run(a.repo, a.base, a.head, a.develop)
    out = json.dumps(res, indent=2, ensure_ascii=False)
    if a.out:
        open(a.out, "w").write(out)
    else:
        print(out)
    sys.exit(0)


if __name__ == "__main__":
    main()
