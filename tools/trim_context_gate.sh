#!/usr/bin/env bash
# trim_context_gate.sh — Step 0 gate for /trim-context-file.
#
# Run BEFORE any edit to an always-loaded context file. Establishes that the
# supporting script's capabilities are real (not merely advertised) and captures
# a verified baseline. Exits 0 only when every check passes.
#
# Usage:
#   bash tools/trim_context_gate.sh <target> [approved_block_count]
#
# WHY THIS EXISTS, AND THE TRAP IT AVOIDS
# ---------------------------------------
# The skill this gates was disabled because its verification invoked script
# flags that did not exist: `--rules --json > rules_before.json` produced a
# ZERO-BYTE file, the later "any normative line present before and absent after
# is a hard abort" check then iterated an EMPTY before-set, found nothing
# missing, and reported PASS. A guard with no failing mode is the same bug
# wearing a safety vest.
#
# So this gate NEVER tests file existence or size. `cmd > f` truncates f BEFORE
# cmd runs, so `[ -f f ]` passes on a failed run and `[ -s f ]` passes on any
# partial write. Both reintroduce the original bug in a new costume. Instead we
# capture stdout into a shell variable — $(...) propagates the child's exit
# status — check the status, THEN parse the content, and write the baseline file
# only once it is known good.
#
# BYTE CONSERVATION IS THE LOAD-BEARING GUARANTEE. The rules diff is
# corroborating only: `--rules` is a keyword+structural SAMPLE, not a cover
# (~71 of 240 non-blank lines on CLAUDE.md), and before/after are parsed by the
# SAME detector, so a systematic omission cancels on both sides. Never invert
# that priority.
#
# Also note: `--require` verifies a DECLARATION, not behavior — delete the
# --rules implementation and leave the constant, and --require still exits 0.
# That is why steps 2 and 3 below EXERCISE the real code paths on the real
# target rather than trusting the capability list.
set -u

CFA=(python3 tools/context_file_audit.py)
TARGET="${1:-CLAUDE.md}"
EXPECTED="${2:-}"
export PYTHONIOENCODING=utf-8

abort() { echo "ABORT (Step 0 gate): $*" >&2; exit 1; }

[ -f "$TARGET" ] || abort "target is not a file: $TARGET"
WORK="$(mktemp -d)" || abort "could not create work dir"

# 1. Capability DECLARATION check. Necessary, not sufficient — see header.
"${CFA[@]}" --require rules,emit-blocks,fence-aware-sections,clean-emit-dir,structural-rules,expect-blocks \
  || abort "required capability set is not advertised by context_file_audit.py"

# 2. EXERCISE --rules on the real target. Status first, then content.
RULES_JSON="$("${CFA[@]}" "$TARGET" --rules --json)" \
  || abort "--rules exited non-zero on $TARGET (empty or unparsable baseline refused)"
RULE_COUNT="$(printf '%s' "$RULES_JSON" |
  python3 -c 'import sys,json; print(json.load(sys.stdin)["rule_count"])')" \
  || abort "--rules produced no parsable JSON"
[ "$RULE_COUNT" -gt 0 ] 2>/dev/null \
  || abort "rule_count=$RULE_COUNT — refusing an empty baseline"
printf '%s' "$RULES_JSON" > "$WORK/rules_before.json"   # written ONLY once good

# 3. EXERCISE --emit-blocks for real, into a fresh directory.
BLOCKS="$WORK/blocks"
if [ -n "$EXPECTED" ]; then
  MANIFEST_JSON="$("${CFA[@]}" "$TARGET" --emit-blocks "$BLOCKS" --expect-blocks "$EXPECTED" --json)" \
    || abort "--emit-blocks failed or block count != approved count ($EXPECTED)"
else
  MANIFEST_JSON="$("${CFA[@]}" "$TARGET" --emit-blocks "$BLOCKS" --json)" \
    || abort "--emit-blocks exited non-zero"
fi

# 4. INDEPENDENT byte-conservation check. The manifest's sha256 is computed over
#    the script's OWN output, so it agrees with itself and proves nothing alone.
#    Re-derive against the SOURCE bytes. THIS is the load-bearing guarantee.
printf '%s' "$MANIFEST_JSON" | python3 -c '
import sys, json, hashlib, pathlib
m = json.load(sys.stdin)
d = pathlib.Path(sys.argv[1]); src = pathlib.Path(sys.argv[2]).read_bytes()
blocks = sorted(m["blocks"], key=lambda b: b["index"])
assert blocks, "zero blocks emitted"
assert m["block_count"] == len(blocks), "block_count disagrees with blocks[]"
joined = b"".join((d / b["file"]).read_bytes() for b in blocks)
assert joined == src, "blocks do not reconstruct the source byte-for-byte"
assert sum(b["bytes"] for b in blocks) == len(src), "byte accounting does not balance"
for b in blocks:
    raw = (d / b["file"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == b["sha256"], f"sha256 mismatch: {b['file']}"
print(f"  byte conservation OK: {len(blocks)} blocks reconstruct {len(src)} source bytes exactly")
' "$BLOCKS" "$TARGET" || abort "emitted blocks are not verbatim slices of the source"

echo "Step 0 gate PASSED for $TARGET"
echo "  rules baseline : $WORK/rules_before.json  (rule_count=$RULE_COUNT)"
echo "  blocks         : $BLOCKS"
echo "  Pass these paths to Step 5. Compare the after-set against the UNION of the"
echo "  trimmed source AND every destination file — not the source alone."
