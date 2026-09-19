package org.videolan.vlc.gui.preferences

import org.videolan.resources.AndroidDevices

/**
 * Stand-in for VLC's PreferenceVisibilityManager: the same when-expression
 * over preference keys, including the TV gate the patch has to relax.
 */
object PreferenceVisibilityManager {
    fun isPreferenceVisible(key: String?, forTv: Boolean = false) = when (key) {
        "video_action_switch" -> !forTv || AndroidDevices.hasPiP
        "popup_force_legacy" -> AndroidDevices.pipAllowed
        "resume_playback" -> !forTv
        else -> true
    }
}
