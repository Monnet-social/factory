#!/usr/bin/env bash
# Run the reviewer headless on every prepared workspace, N in parallel.
# Each run: cwd = workspace, project settings only, no MCP, no Bash/network tools.
# Usage: run_reviews.sh <runs dir> [parallel=4] [model=opus]
set -uo pipefail
RUNS=${1:?runs dir}; PAR=${2:-4}; MODEL=${3:-opus}

PROMPT='Use the monnett-core-review skill to review the pull request prepared in this workspace
(pr/ holds the PR data, core/ the repository at the PR head). Follow the skill procedure exactly and
write the result to review.json in this directory. Write all finding text in English.'

review_one() {
  ws=$1
  [ -s "$ws/review.json" ] && { echo "skip $(basename "$ws")"; return; }
  start=$(date +%s)
  ( cd "$ws" && claude -p "$PROMPT" \
      --model "$MODEL" \
      --setting-sources project \
      --strict-mcp-config \
      --no-session-persistence \
      --allowedTools "Read,Grep,Glob,Write,Skill" \
      --disallowedTools "Bash,WebFetch,WebSearch,Agent,Edit,NotebookEdit" \
      --output-format json > run.json 2> run.err )
  echo "$(basename "$ws") exit=$? $(( $(date +%s) - start ))s review=$([ -s "$ws/review.json" ] && echo yes || echo NO)"
}
export -f review_one; export PROMPT MODEL
ls -d "$RUNS"/PR-* | xargs -P "$PAR" -I{} bash -c 'review_one "$@"' _ {}
