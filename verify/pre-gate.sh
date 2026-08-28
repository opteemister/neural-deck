#!/usr/bin/env bash
# Deterministic pre-gate: zero-token mechanical checks before launching the verifier.
# Run by the orchestrator inside the task worktree after the developer commits.
# RED output goes straight back to the developer as the gap list; the verifier
# is launched only when this script is GREEN.
#
# Checks:
#   1. Canonical verify (verify/ restored from the base branch, then run-verify.sh)
#   2. File length limit for changed files (constitution IV.8)
#   3. Diff stays within the task's code_area
#
# Usage: verify/pre-gate.sh <base-branch> [task-file]
#   base-branch — branch the worktree was created from (canon source + diff base)
#   task-file   — task markdown with a code_area frontmatter list (skip check if omitted)
# Env:
#   PRE_GATE_MAX_LINES — line limit per changed file (default 500; set 0 to disable
#                        for intentionally compact projects recorded in AGENTS.md)
set -u

BASE="${1:?usage: verify/pre-gate.sh <base-branch> [task-file]}"
TASK_FILE="${2:-}"
MAX_LINES="${PRE_GATE_MAX_LINES:-500}"

fail=0

echo "== pre-gate 1/3: canonical verify =="
if ! git restore --source="$BASE" -- verify/; then
    echo "FAIL: cannot restore canonical verify/ from '$BASE'"
    exit 1
fi
if ./verify/run-verify.sh; then
    echo "PASS: canonical verify"
else
    echo "FAIL: canonical verify — the full output above is the developer's input for the fix"
    fail=1
fi

CHANGED=$(git diff --name-only "$BASE"...HEAD)

echo ""
echo "== pre-gate 2/3: file length (limit: $MAX_LINES) =="
if [ "$MAX_LINES" -eq 0 ]; then
    echo "SKIP: limit disabled for this project (see AGENTS.md)"
else
    over=0
    IFS='
'
    for f in $CHANGED; do
        [ -f "$f" ] || continue
        case "$f" in
            *.md|*.lock|*lock.json|*lock.yaml|*lock.yml) continue ;;
        esac
        grep -Iq . "$f" 2>/dev/null || continue # skip binary files
        lines=$(wc -l < "$f" | tr -d ' ')
        if [ "$lines" -gt "$MAX_LINES" ]; then
            echo "FAIL: $f has $lines lines (limit $MAX_LINES, constitution IV.8)"
            over=1
            fail=1
        fi
    done
    unset IFS
    [ "$over" -eq 0 ] && echo "PASS: all changed files within the limit"
fi

echo ""
echo "== pre-gate 3/3: diff within code_area =="
if [ -z "$TASK_FILE" ]; then
    echo "SKIP: no task file provided"
elif [ ! -f "$TASK_FILE" ]; then
    echo "FAIL: task file not found: $TASK_FILE"
    fail=1
else
    # Extract code_area entries from the task frontmatter.
    # Supports inline `code_area: [a, b]` and block lists (`- a` lines).
    AREAS=$(awk '
        /^code_area:/ {
            line = $0
            sub(/^code_area:[ \t]*/, "", line)
            if (line ~ /^\[/) {
                gsub(/[][\042\047]/, "", line)
                n = split(line, parts, ",")
                for (i = 1; i <= n; i++) {
                    gsub(/^[ \t]+|[ \t]+$/, "", parts[i])
                    if (parts[i] != "") print parts[i]
                }
                exit
            }
            inlist = 1
            next
        }
        inlist {
            if ($0 ~ /^[ \t]*-[ \t]/) {
                line = $0
                sub(/^[ \t]*-[ \t]*/, "", line)
                gsub(/[\042\047]/, "", line)
                if (line != "") print line
            } else if ($0 !~ /^[ \t]*$/) {
                exit
            }
        }
    ' "$TASK_FILE")
    if [ -z "$AREAS" ]; then
        echo "FAIL: no code_area found in $TASK_FILE"
        fail=1
    else
        out=0
        IFS='
'
        for f in $CHANGED; do
            ok=0
            for area in $AREAS; do
                area="${area%/}"
                case "$f" in
                    "$area"|"$area"/*) ok=1; break ;;
                esac
            done
            if [ "$ok" -eq 0 ]; then
                echo "FAIL: $f is outside the task code_area"
                out=1
                fail=1
            fi
        done
        unset IFS
        [ "$out" -eq 0 ] && echo "PASS: diff stays within code_area"
    fi
fi

echo ""
if [ "$fail" -eq 0 ]; then
    echo "pre-gate: GREEN — ready for the verifier"
else
    echo "pre-gate: RED — return this output to the developer; do NOT launch the verifier"
fi
exit "$fail"
