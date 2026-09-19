#!/usr/bin/env python3
"""Enable Picture-in-Picture on Android TV builds of VLC for Android.

Operates on baksmali output of the official (unminified) VLC APK.

Two upstream gates stop PiP from being usable on a TV:

1. ``AndroidDevices.pipAllowed`` is false whenever the system does not report
   ``FEATURE_PICTURE_IN_PICTURE``.  ``VideoPlayerActivity.switchToPopup()``
   returns early in that case and the player options menu hides its
   popup/PiP button.
2. ``PreferenceVisibilityManager.isPreferenceVisible()`` hides the
   ``video_action_switch`` preference ("when leaving the app") on the TV UI
   unless ``AndroidDevices.hasPiP`` is true, so the option cannot be turned on.

This patch forces ``pipAllowed`` to true and makes the preference visibility
test read ``pipAllowed`` instead of ``hasPiP``.  ``hasPiP`` itself is left
alone on purpose: ``switchToPopup()`` only calls the framework
``enterPictureInPictureMode()`` when ``hasPiP`` is true, and falls back to
VLC's own overlay ("legacy popup") window otherwise.  Forcing ``hasPiP``
would make VLC call a framework API the TV may not implement.
"""

import argparse
import re
import sys
from pathlib import Path

ANDROID_DEVICES = "org/videolan/resources/AndroidDevices.smali"
PREF_VISIBILITY = "org/videolan/vlc/gui/preferences/PreferenceVisibilityManager.smali"
OWNER = "Lorg/videolan/resources/AndroidDevices;"


def log(msg):
    print(f"[patch_smali] {msg}", flush=True)


def find_class(roots, rel):
    for root in roots:
        candidate = Path(root) / rel
        if candidate.is_file():
            return candidate
    return None


def report_references(roots, needles):
    """Print every smali reference to the symbols we care about."""
    for needle in needles:
        hits = []
        for root in roots:
            for path in Path(root).rglob("*.smali"):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                count = text.count(needle)
                if count:
                    hits.append((path, count))
        log(f"references to {needle}: {sum(c for _, c in hits)} in {len(hits)} file(s)")
        for path, count in hits[:20]:
            log(f"    {count:3d}  {path}")


def force_getter_true(path, method):
    """Rewrite ``method()Z`` so that it unconditionally returns true."""
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^(\.method\b[^\n]*?\b" + re.escape(method) + r"\(\)Z[^\n]*\n)(.*?)(^\.end method$)",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        log(f"getter {method}(): found {len(matches)} definition(s) in {path.name}")
        return False
    body = "    .locals 1\n\n    const/4 v0, 0x1\n\n    return v0\n\n"
    text = text[: matches[0].start(2)] + body + text[matches[0].end(2):]
    path.write_text(text, encoding="utf-8")
    log(f"getter {method}() in {path.name} now returns true")
    return True


def const_true(register):
    """Pick a const instruction that can address ``register``."""
    number = int(register[1:])
    opcode = "const/4" if number <= 15 else "const/16"
    return f"{opcode} {register}, 0x1"


def force_field_true(path, field):
    """Force every ``sput-boolean`` into ``field`` to store true."""
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^([ \t]*)sput-boolean (v\d+), " + re.escape(OWNER) + re.escape(f"->{field}:Z") + r"\s*$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        log(f"field {field}: no sput-boolean found in {path.name}")
        return False
    def replace(match):
        indent, register = match.group(1), match.group(2)
        return f"{indent}{const_true(register)}\n\n{match.group(0)}"
    text = pattern.sub(replace, text)
    path.write_text(text, encoding="utf-8")
    log(f"field {field} in {path.name}: forced true at {len(matches)} assignment(s)")
    return True


def retarget(path, old, new, label):
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if not count:
        log(f"{label}: '{old}' not found in {path.name}")
        return False
    path.write_text(text.replace(old, new), encoding="utf-8")
    log(f"{label}: rewrote {count} reference(s) in {path.name}")
    return True


def main_with_roots(roots, report_only=False):
    """Apply every patch to the given baksmali output directories.

    Returns 0 on success, a non-zero status naming the failed step otherwise.
    """
    roots = [Path(r) for r in roots]
    report_references(
        roots,
        [
            f"{OWNER}->getPipAllowed()Z",
            f"{OWNER}->pipAllowed:Z",
            f"{OWNER}->getHasPiP()Z",
            f"{OWNER}->hasPiP:Z",
        ],
    )
    if report_only:
        return 0

    devices = find_class(roots, ANDROID_DEVICES)
    if devices is None:
        log(f"FATAL: {ANDROID_DEVICES} not found in {[str(r) for r in roots]}")
        return 2
    log(f"AndroidDevices: {devices}")

    # Either patch alone is enough; apply both so the result does not depend on
    # whether callers read the backing field or go through the getter.
    getter_patched = force_getter_true(devices, "getPipAllowed")
    field_patched = force_field_true(devices, "pipAllowed")
    if not (getter_patched or field_patched):
        log("FATAL: could not force AndroidDevices.pipAllowed to true")
        return 3

    visibility = find_class(roots, PREF_VISIBILITY)
    if visibility is None:
        log(f"FATAL: {PREF_VISIBILITY} not found")
        return 4
    log(f"PreferenceVisibilityManager: {visibility}")

    # The TV UI hides the "when leaving the app" list preference behind
    # hasPiP; make that test read the (now always true) pipAllowed instead.
    visibility_patched = retarget(
        visibility,
        f"{OWNER}->getHasPiP()Z",
        f"{OWNER}->getPipAllowed()Z",
        "preference visibility (getter)",
    ) | retarget(
        visibility,
        f"{OWNER}->hasPiP:Z",
        f"{OWNER}->pipAllowed:Z",
        "preference visibility (field)",
    )
    if not visibility_patched:
        log("FATAL: could not relax the TV visibility test for video_action_switch")
        return 5

    log("all patches applied")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", help="baksmali output directories")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    return main_with_roots(args.roots, report_only=args.report_only)


if __name__ == "__main__":
    sys.exit(main())
