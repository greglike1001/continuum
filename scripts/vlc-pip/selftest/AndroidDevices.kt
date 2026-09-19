package org.videolan.resources

/**
 * Stand-in for VLC's org.videolan.resources.AndroidDevices, reduced to the
 * shape the patch depends on: a Kotlin object whose Boolean vals become
 * private static fields plus public getters, with pipAllowed derived in the
 * initialiser exactly as upstream derives it.
 */
object AndroidDevices {
    val hasTsp: Boolean
    val hasPiP: Boolean
    val pipAllowed: Boolean
    val isAndroidTv: Boolean

    init {
        hasTsp = System.nanoTime() > 0
        isAndroidTv = System.nanoTime() > 1
        hasPiP = System.nanoTime() > 2
        pipAllowed = hasPiP || (hasTsp && !isOOrLater())
    }

    private fun isOOrLater() = System.nanoTime() > 3
}
