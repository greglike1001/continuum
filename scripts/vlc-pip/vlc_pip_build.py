#!/usr/bin/env python3
"""Build a VLC for Android APK with Picture-in-Picture unlocked on Android TV.

Downloads the official release APK from VideoLAN, patches the two bytecode
gates that hide PiP on a TV (see patch_smali.py), repacks and re-signs it.
Needs only Python 3 and a JVM; everything else is fetched on demand.

    python3 vlc_pip_build.py --abi arm64-v8a

Runs the same on Windows, macOS and Linux.
"""

import argparse
import hashlib
import importlib.util
import html.parser
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VLC_BASE = "https://get.videolan.org/vlc-android"

TOOLS = {
    "baksmali.jar": (
        "https://bitbucket.org/JesusFreke/smali/downloads/baksmali-2.5.2.jar",
        "d3116248cce4f82ec5a31eb7f95ee75daff42ddf6eed0ab573973dc53fbad2e5",
    ),
    "smali.jar": (
        "https://bitbucket.org/JesusFreke/smali/downloads/smali-2.5.2.jar",
        "9544299578b16f771d8aa8eaefe0d3718ca03478c16f3c356f2fcf1366bfb116",
    ),
    "uber-apk-signer.jar": (
        "https://github.com/patrickfav/uber-apk-signer/releases/download/v1.3.0/uber-apk-signer-1.3.0.jar",
        "e1299fd6fcf4da527dd53735b56127e8ea922a321128123b9c32d619bba1d835",
    ),
}

# Classes the patch touches; only the dex parts holding them are rebuilt.
TARGET_CLASSES = (
    "Lorg/videolan/resources/AndroidDevices;",
    "Lorg/videolan/vlc/gui/preferences/PreferenceVisibilityManager;",
)

# dex format version in the file header -> API level to assemble for.
DEX_API = {"035": 21, "037": 24, "038": 26, "039": 28, "040": 30}

SIGNATURE_ENTRIES = re.compile(r"^META-INF/(.*\.(SF|RSA|DSA|EC)|MANIFEST\.MF)$")


def log(msg):
    print(f"[vlc-pip] {msg}", flush=True)


def run(cmd):
    log("$ " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url, dest, expected=None):
    log(f"downloading {url}")
    with urllib.request.urlopen(url) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)
    got = sha256(dest)
    if expected and got != expected:
        raise SystemExit(f"checksum mismatch for {url}\n  expected {expected}\n  got      {got}")
    log(f"  {dest.name}  {got}")
    return dest


class LinkParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)


def list_links(url):
    with urllib.request.urlopen(url) as response:
        body = response.read().decode("utf-8", "replace")
    parser = LinkParser()
    parser.feed(body)
    return parser.links


def newest_version():
    versions = []
    for link in list_links(VLC_BASE + "/"):
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)/?", link.strip("/") + "/")
        if match:
            versions.append(tuple(int(p) for p in match.groups()))
    if not versions:
        raise SystemExit(f"no release directories found at {VLC_BASE}/")
    return ".".join(str(p) for p in max(versions))


def download_apk(version, abi, dest_dir):
    listing = list_links(f"{VLC_BASE}/{version}/")
    candidates = sorted({link for link in listing if link.endswith(".apk") and abi in link})
    if not candidates:
        raise SystemExit(f"no APK for ABI {abi} at {VLC_BASE}/{version}/")
    name = candidates[0]
    apk = fetch(f"{VLC_BASE}/{version}/{name}", dest_dir / name)
    try:
        checksum = dest_dir / (name + ".sha256")
        fetch(f"{VLC_BASE}/{version}/{name}.sha256", checksum)
        expected = checksum.read_text().split()[0]
        if sha256(apk) != expected:
            raise SystemExit(f"{name} does not match the published checksum")
        log("published checksum verified")
    except SystemExit:
        raise
    except Exception:
        log("no published .sha256 alongside the APK; skipping that check")
    return apk


def dex_api(path):
    with open(path, "rb") as handle:
        version = handle.read(8)[4:7].decode("ascii", "replace")
    if version not in DEX_API:
        raise SystemExit(f"unknown dex format version '{version}' in {path.name}")
    return DEX_API[version]


def load_patcher():
    module_path = Path(__file__).resolve().parent / "patch_smali.py"
    if not module_path.is_file():
        raise SystemExit(f"patch_smali.py not found next to {Path(__file__).name}")
    spec = importlib.util.spec_from_file_location("patch_smali", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patch_apk(apk, out_apk, tools, work):
    java = shutil.which("java")
    if not java:
        raise SystemExit("java not found on PATH; install a JDK (Temurin, or your distro's openjdk)")

    dex_dir = work / "dex"
    dex_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk) as archive:
        dex_names = [n for n in archive.namelist() if re.fullmatch(r"classes\d*\.dex", n)]
        for name in dex_names:
            with archive.open(name) as src, open(dex_dir / name, "wb") as dst:
                shutil.copyfileobj(src, dst)
    log(f"APK holds {len(dex_names)} dex part(s): {', '.join(dex_names)}")

    interesting = []
    for name in dex_names:
        blob = (dex_dir / name).read_bytes()
        if any(cls.encode() in blob for cls in TARGET_CLASSES):
            interesting.append(name)
    if not interesting:
        raise SystemExit("none of the dex parts contain the classes this patch targets")
    log(f"patching {', '.join(interesting)}")

    for name in interesting:
        run([java, "-Xmx2g", "-jar", tools["baksmali.jar"], "d", dex_dir / name,
             "-o", work / "smali" / name])

    patcher = load_patcher()
    status = patcher.main_with_roots([work / "smali" / name for name in interesting])
    if status != 0:
        raise SystemExit(f"patching failed with status {status}")

    patched_dir = work / "patched"
    patched_dir.mkdir(parents=True, exist_ok=True)
    for name in interesting:
        run([java, "-Xmx4g", "-jar", tools["smali.jar"], "a", work / "smali" / name,
             "-o", patched_dir / name, "--api", dex_api(dex_dir / name)])

    # Rewrite the archive: patched dex in, old signature out, everything else
    # copied with its original compression so resources.arsc and the native
    # libraries stay stored.
    replacements = {name: (patched_dir / name).read_bytes() for name in interesting}
    with zipfile.ZipFile(apk) as src, zipfile.ZipFile(out_apk, "w") as dst:
        for info in src.infolist():
            if SIGNATURE_ENTRIES.match(info.filename):
                continue
            data = replacements.get(info.filename)
            if data is None:
                data = src.read(info.filename)
            target = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            target.compress_type = info.compress_type
            target.external_attr = info.external_attr
            target.internal_attr = info.internal_attr
            target.create_system = info.create_system
            dst.writestr(target, data)
    log(f"repacked {out_apk.name}")


def sign(out_apk, tools, keystore, ks_pass, ks_alias):
    java = shutil.which("java")
    cmd = [java, "-jar", tools["uber-apk-signer.jar"], "--apks", out_apk, "--overwrite", "--allowResign"]
    if keystore:
        cmd += ["--ks", keystore, "--ksAlias", ks_alias, "--ksPass", ks_pass, "--ksKeyPass", ks_pass]
    run(cmd)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="VLC version (default: newest on get.videolan.org)")
    parser.add_argument("--abi", action="append", default=[],
                        help="ABI to build, repeatable (default: arm64-v8a)")
    parser.add_argument("--input", type=Path, help="patch this local APK instead of downloading")
    parser.add_argument("--out-dir", type=Path, default=Path("vlc-pip-out"))
    parser.add_argument("--tools-dir", type=Path, default=Path("vlc-pip-tools"))
    parser.add_argument("--keystore", help="keystore to sign with (default: uber-apk-signer's debug key)")
    parser.add_argument("--ks-pass", default="")
    parser.add_argument("--ks-alias", default="")
    parser.add_argument("--no-sign", action="store_true", help="stop after repacking (for tests)")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.tools_dir.mkdir(parents=True, exist_ok=True)

    tools = {}
    for name, (url, digest) in TOOLS.items():
        if name == "uber-apk-signer.jar" and args.no_sign:
            continue
        path = args.tools_dir / name
        if path.is_file() and sha256(path) == digest:
            log(f"{name} already present")
        else:
            fetch(url, path, digest)
        tools[name] = path

    if args.input:
        sources = [args.input]
    else:
        version = args.version or newest_version()
        log(f"VLC version {version}")
        abis = args.abi or ["arm64-v8a"]
        sources = [download_apk(version, abi, args.out_dir) for abi in abis]

    results = []
    for apk in sources:
        out_apk = args.out_dir / (apk.stem + "-pip.apk")
        with tempfile.TemporaryDirectory() as tmp:
            patch_apk(apk, out_apk, tools, Path(tmp))
        if not args.no_sign:
            sign(out_apk, tools, args.keystore, args.ks_pass, args.ks_alias)
        results.append(out_apk)

    log("done")
    for apk in results:
        print(f"    {apk}  sha256 {sha256(apk)}")
    if not args.keystore and not args.no_sign:
        print("\nSigned with uber-apk-signer's debug key. Uninstall the official VLC")
        print("before installing this build: same package name, different signature.")


if __name__ == "__main__":
    sys.exit(main())
