#!/usr/bin/env bash
#
# Rebuild an official VLC for Android APK with Picture-in-Picture enabled on
# Android TV, then re-sign it.
#
# Usage: patch-vlc-pip.sh <input.apk> <output.apk>
#
# Environment:
#   BAKSMALI_JAR  path to baksmali.jar                           [required]
#   SMALI_JAR     path to smali.jar                              [required]
#   BUILD_TOOLS   Android build-tools dir (zipalign, apksigner) [default: on PATH]
#   KEYSTORE      keystore used to sign the result              [required]
#   KEYSTORE_PASS keystore and key password                     [required]
#   KEY_ALIAS     key alias                                     [default: vlcpip]
#
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <input.apk> <output.apk>" >&2
    exit 64
fi

input=$(readlink -f "$1")
output=$2
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

: "${BAKSMALI_JAR:?set BAKSMALI_JAR to the baksmali jar}"
: "${SMALI_JAR:?set SMALI_JAR to the smali jar}"
: "${KEYSTORE:?set KEYSTORE to the signing keystore}"
: "${KEYSTORE_PASS:?set KEYSTORE_PASS to the keystore password}"
KEY_ALIAS=${KEY_ALIAS:-vlcpip}
BAKSMALI_JAR=$(readlink -f "$BAKSMALI_JAR")
SMALI_JAR=$(readlink -f "$SMALI_JAR")
KEYSTORE=$(readlink -f "$KEYSTORE")

if [ -n "${BUILD_TOOLS:-}" ]; then
    zipalign_bin="$BUILD_TOOLS/zipalign"
    apksigner_bin="$BUILD_TOOLS/apksigner"
else
    zipalign_bin=$(command -v zipalign)
    apksigner_bin=$(command -v apksigner)
fi
[ -x "$zipalign_bin" ] || { echo "zipalign not found (set BUILD_TOOLS)" >&2; exit 69; }
[ -x "$apksigner_bin" ] || { echo "apksigner not found (set BUILD_TOOLS)" >&2; exit 69; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "== input APK"
sha256sum "$input"
"$apksigner_bin" verify --print-certs "$input" | sed 's/^/    /'

echo "== extracting dex"
mkdir -p "$work/dex"
unzip -o -q "$input" 'classes*.dex' -d "$work/dex"
ls -l "$work/dex"

# The dex format version in the file header tells smali which API level to
# assemble for, so the rebuilt dex keeps the format the original was built in.
dex_api() {
    local version
    version=$(head -c 8 "$1" | tail -c 4 | tr -d '\0')
    case "$version" in
        035) echo 21 ;;
        037) echo 24 ;;
        038) echo 26 ;;
        039) echo 28 ;;
        040) echo 30 ;;
        *)   echo "unknown dex version '$version' in $1" >&2; return 1 ;;
    esac
}

# Only disassemble the dex files that mention the classes we patch: VLC ships
# several multidex parts and each one takes a while to round-trip.
declare -a smali_dirs=()
declare -a touched=()
for dex in "$work"/dex/classes*.dex; do
    name=$(basename "$dex")
    if ! grep -qa 'Lorg/videolan/resources/AndroidDevices;\|Lorg/videolan/vlc/gui/preferences/PreferenceVisibilityManager;' "$dex"; then
        echo "   $name: no relevant classes, left untouched"
        continue
    fi
    echo "== disassembling $name (dex api $(dex_api "$dex"))"
    java -Xmx2g -jar "$BAKSMALI_JAR" d "$dex" -o "$work/smali/$name"
    smali_dirs+=("$work/smali/$name")
    touched+=("$name")
done
[ "${#smali_dirs[@]}" -gt 0 ] || { echo "no dex contained the VLC classes to patch" >&2; exit 70; }

echo "== patching"
python3 "$here/patch_smali.py" "${smali_dirs[@]}"

echo "== reassembling"
mkdir -p "$work/patched"
for name in "${touched[@]}"; do
    api=$(dex_api "$work/dex/$name")
    java -Xmx4g -jar "$SMALI_JAR" a "$work/smali/$name" -o "$work/patched/$name" --api "$api"
    ls -l "$work/patched/$name"
done

echo "== repacking"
# Start from the original APK so every untouched entry stays byte for byte
# identical, then swap in the rebuilt dex files and drop the old signature.
cp "$input" "$work/unsigned.apk"
chmod u+w "$work/unsigned.apk"
zip -q -d "$work/unsigned.apk" 'META-INF/*.SF' 'META-INF/*.RSA' 'META-INF/*.DSA' 'META-INF/MANIFEST.MF' || true
( cd "$work/patched" && zip -q -X "$work/unsigned.apk" "${touched[@]}" )

echo "== aligning"
"$zipalign_bin" -p -f 4 "$work/unsigned.apk" "$work/aligned.apk"
"$zipalign_bin" -c -p -v 4 "$work/aligned.apk" >/dev/null

echo "== signing"
"$apksigner_bin" sign \
    --ks "$KEYSTORE" \
    --ks-pass "pass:$KEYSTORE_PASS" \
    --key-pass "pass:$KEYSTORE_PASS" \
    --ks-key-alias "$KEY_ALIAS" \
    --v1-signing-enabled true \
    --v2-signing-enabled true \
    --v3-signing-enabled true \
    --out "$output" \
    "$work/aligned.apk"

echo "== result"
"$apksigner_bin" verify --print-certs "$output" | sed 's/^/    /'
sha256sum "$output"
