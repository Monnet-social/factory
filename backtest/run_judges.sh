#!/usr/bin/env bash
# Judge every reviewed workspace: copy first-round human comments + the post-review patch in, run the judge.
# Usage: run_judges.sh <runs dir> <selection.json> <core repo> [parallel=4] [model=opus]
set -uo pipefail
RUNS=${1:?}; SEL=${2:?}; REPO=${3:?}; PAR=${4:-4}; MODEL=${5:-opus}
HERE=$(cd "$(dirname "$0")" && pwd)

python3 -I - "$RUNS" "$SEL" "$REPO" <<'EOF'
import json, os, subprocess, sys
runs, sel, repo = sys.argv[1:4]
for x in json.load(open(sel))["selected"]:
    ws = os.path.join(runs, f"PR-{x['pr']}")
    if not os.path.exists(os.path.join(ws, "review.json")):
        continue
    json.dump(x["human_first_round"], open(os.path.join(ws, "human.json"), "w"), indent=1, ensure_ascii=False)
    patch = ""
    if x.get("final_head") and x["final_head"] != x["head"]:
        patch = subprocess.run(["git", "-C", repo, "diff", "-M", x["head"], x["final_head"]],
                               capture_output=True, text=True).stdout
    open(os.path.join(ws, "pr", "after-review.patch"), "w").write(patch)
EOF

judge_one() {
  ws=$1
  [ -s "$ws/review.json" ] || return
  [ -s "$ws/judge.json" ] && { echo "skip $(basename "$ws")"; return; }
  ( cd "$ws" && claude -p "$(cat "$HERE/judge.md")" \
      --model "$MODEL" --setting-sources project --strict-mcp-config --no-session-persistence \
      --allowedTools "Read,Grep,Glob,Write" \
      --disallowedTools "Bash,WebFetch,WebSearch,Agent,Edit,NotebookEdit,Skill" \
      --output-format json > judge-run.json 2> judge-run.err )
  echo "$(basename "$ws") judge exit=$? judge=$([ -s "$ws/judge.json" ] && echo yes || echo NO)"
}
export -f judge_one; export HERE MODEL
ls -d "$RUNS"/PR-* | xargs -P "$PAR" -I{} bash -c 'judge_one "$@"' _ {}
