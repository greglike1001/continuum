#!/usr/bin/env bash
#
# Prove patch_smali.py against real Kotlin bytecode without needing the VLC
# APK: compile the stubs in selftest/, dex them, disassemble, patch,
# reassemble, and check the result.
#
# Environment:
#   KOTLINC       kotlinc executable          [required]
#   DX_JAR        dalvik dx jar               [required]
#   BAKSMALI_JAR  baksmali jar                [required]
#   SMALI_JAR     smali jar                   [required]
#
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
: "${KOTLINC:?}" "${DX_JAR:?}" "${BAKSMALI_JAR:?}" "${SMALI_JAR:?}"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "== compiling stubs"
JAVA_TOOL_OPTIONS= "$KOTLINC" "$here/selftest" -d "$work/classes" -jvm-target 1.8 -nowarn

echo "== dexing"
java -cp "$DX_JAR" com.android.dx.command.Main --dex --min-sdk-version=21 \
    --output="$work/classes.dex" "$work/classes"

echo "== disassembling"
java -jar "$BAKSMALI_JAR" d "$work/classes.dex" -o "$work/smali"

echo "== patching"
python3 "$here/patch_smali.py" "$work/smali"

echo "== reassembling"
java -jar "$SMALI_JAR" a "$work/smali" -o "$work/patched.dex" --api 21

echo "== checking result"
java -jar "$BAKSMALI_JAR" d "$work/patched.dex" -o "$work/verify"
devices="$work/verify/org/videolan/resources/AndroidDevices.smali"
visibility="$work/verify/org/videolan/vlc/gui/preferences/PreferenceVisibilityManager.smali"

fail=0
check() {
    if eval "$2"; then
        echo "  ok    $1"
    else
        echo "  FAIL  $1"
        fail=1
    fi
}

getter=$(sed -n '/^\.method public final getPipAllowed()Z$/,/^\.end method$/p' "$devices")
check "getPipAllowed() returns a constant" '[ -n "$(echo "$getter" | grep "const/4 v0, 0x1")" ]'
check "getPipAllowed() no longer reads the field" '[ -z "$(echo "$getter" | grep "sget-boolean")" ]'
check "pipAllowed is assigned true" 'grep -B2 "sput-boolean v., Lorg/videolan/resources/AndroidDevices;->pipAllowed:Z" "$devices" | grep -q "const/4 v., 0x1"'
check "visibility test no longer calls getHasPiP()" '! grep -q "getHasPiP()Z" "$visibility"'
check "visibility test calls getPipAllowed()" 'grep -q "getPipAllowed()Z" "$visibility"'

[ "$fail" -eq 0 ] || { echo "selftest failed"; exit 1; }
echo "selftest passed"
