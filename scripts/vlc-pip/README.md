# VLC for Android with Picture-in-Picture unlocked on Android TV

Produces a sideloadable build of **official VLC for Android** in which the
Picture-in-Picture option is available on a TV (Sony Bravia, Google TV,
Chromecast with Google TV, Shield, etc.).

Nothing here builds VLC from source. The official release APK is downloaded
from `https://get.videolan.org/vlc-android/`, two gates in its bytecode are
relaxed, and the result is re-signed. Every other byte of the app is the one
VideoLAN published.

## Why the patch is needed

VLC already contains the whole PiP implementation. Two checks hide it:

| Upstream code | Effect on a TV |
| --- | --- |
| `AndroidDevices.pipAllowed = hasPiP \|\| (hasTsp && !isOOrLater)` | False unless the system reports `FEATURE_PICTURE_IN_PICTURE`; `VideoPlayerActivity.switchToPopup()` then returns immediately and the player's options menu drops its PiP button. |
| `PreferenceVisibilityManager.isPreferenceVisible()`: `KEY_VIDEO_APP_SWITCH -> !forTv \|\| AndroidDevices.hasPiP` | Hides the *"When leaving the app"* setting (`video_action_switch`) from the TV settings screen, so PiP cannot be switched on. |

The patch:

1. forces `AndroidDevices.pipAllowed` to true (both the backing field
   assignment in `<clinit>` and the `getPipAllowed()` getter), and
2. points the TV visibility test at `pipAllowed` instead of `hasPiP`.

`hasPiP` itself is deliberately left untouched. `switchToPopup()` calls the
framework `enterPictureInPictureMode()` only when `hasPiP` is true and falls
back to VLC's own floating window ("legacy popup") otherwise, so a TV that
does not implement framework PiP gets the overlay window instead of a crash.

## Building

Push to the branch, or run the **VLC PiP APK** workflow manually
(`.github/workflows/vlc-pip-apk.yml`). It patches `arm64-v8a` and
`armeabi-v7a` by default and uploads the APKs plus the keystore as the
`vlc-pip-apk` artifact.

Locally:

```sh
export BAKSMALI_JAR=/path/to/baksmali-2.5.2.jar
export SMALI_JAR=/path/to/smali-2.5.2.jar
export BUILD_TOOLS=$ANDROID_SDK_ROOT/build-tools/35.0.0
export KEYSTORE=/path/to/vlcpip.keystore KEYSTORE_PASS=vlcpip KEY_ALIAS=vlcpip
scripts/vlc-pip/patch-vlc-pip.sh VLC-Android-3.7.1-arm64-v8a.apk VLC-PiP.apk
```

The script fails loudly if the classes or methods it expects are not in the
APK, which is what should happen when a future VLC release reshuffles them.

Only the dex entries that hold the two patched classes are rebuilt. Every
other entry in the APK is copied across untouched, then the archive is
zipaligned and re-signed with v1, v2 and v3 signatures.

## Self-test

`selftest.sh` compiles the Kotlin stubs in `selftest/` (same object shape and
same visibility check as upstream), dexes them, and runs the patcher over the
result, asserting that `getPipAllowed()` becomes a constant, that the
`pipAllowed` field is assigned true, and that the visibility check no longer
calls `getHasPiP()`. It needs `kotlinc`, dx, smali and baksmali:

```sh
KOTLINC=.../kotlinc/bin/kotlinc DX_JAR=.../dalvik-dx.jar \
BAKSMALI_JAR=.../baksmali-2.5.2.jar SMALI_JAR=.../smali-2.5.2.jar \
scripts/vlc-pip/selftest.sh
```

The workflow runs it as its own job, so the patch logic is verified even when
VideoLAN's download server is unreachable.

## Installing on the TV

1. On the TV: **Settings → System → About → Build** (press several times) to
   enable developer options, then allow installs from unknown sources for the
   app you will use to sideload.
2. Uninstall the Play Store / official copy of VLC first. The patched APK
   keeps the package name `org.videolan.vlc` but carries a different
   signature, so Android refuses to install it over the official one.
   Playback history and settings are lost with the uninstall.
3. Sideload `VLC-Android-<version>-arm64-v8a-pip.apk` (use the
   `armeabi-v7a` build if the TV rejects the 64 bit one) with Downloader,
   Send Files to TV, a USB stick, or `adb install`.

## Turning PiP on

**VLC → Settings → Video → When leaving the app → Play video in
Picture-in-Picture mode.** Playback then shrinks into a corner window when
the Home button is pressed during video playback. The player's options menu
(the ⋮ / long-press menu during playback) also gains a **Popup** button that
sends the current video to PiP immediately.

If the TV has no framework PiP support, VLC uses its own overlay window,
which needs the "display over other apps" permission. Grant it in
**Settings → Apps → Special app access → Display over other apps**, or over
adb:

```sh
adb shell appops set org.videolan.vlc SYSTEM_ALERT_WINDOW allow
```

There is also a **Force legacy popup** checkbox under Settings → Video once
the patch is applied, which forces the overlay window even where framework
PiP exists.

## Caveats

- The APK is signed with a throwaway key, so it will never update from the
  Play Store or from VLC's own updater. Rebuild with the same keystore to
  update in place; a different key means uninstall and reinstall.
- Some TVs (Sony included on parts of the range) do not implement framework
  PiP at all. The patch removes VLC's own restrictions; it cannot add
  platform support that is not there. The legacy overlay path above is the
  fallback in that case.
- VLC is GPL. This is a modified build for personal use, and it should not
  be redistributed as "VLC".
