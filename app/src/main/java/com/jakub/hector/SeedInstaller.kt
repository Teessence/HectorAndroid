package com.jakub.hector

import android.content.Context
import java.io.File

/**
 * Installs the bundled data into writable storage.
 *
 * Two independent concerns, so app updates never destroy user data:
 *  - **Database**: copied only if it doesn't already exist. Once the user has a
 *    hector.db on the phone we never overwrite it.
 *  - **App files** (templates/ and static/): these are shipped code, not user
 *    data, so they are re-copied whenever APP_FILES_VERSION increases. Copying
 *    overwrites the bundled files but never deletes extras, so user-uploaded
 *    ingredient photos in static/ingredient_images are preserved.
 *
 * Bump APP_FILES_VERSION whenever the bundled templates or static assets change.
 */
object SeedInstaller {

    private const val APP_FILES_VERSION = 3

    fun ensureSeed(ctx: Context, dataDir: File) {
        dataDir.mkdirs()

        // 1. Database — seed once, then leave it alone forever.
        val db = File(dataDir, "hector.db")
        if (!db.exists()) {
            copyAssetFile(ctx, "seed/hector.db", db)
        }

        // 2. App files — refresh when the bundled version is newer.
        val marker = File(dataDir, ".appfiles_version")
        val installed = if (marker.exists()) marker.readText().trim().toIntOrNull() ?: 0 else 0
        if (installed < APP_FILES_VERSION) {
            copyAssetTree(ctx, "seed/templates", File(dataDir, "templates"))
            copyAssetTree(ctx, "seed/static", File(dataDir, "static"))
            marker.writeText(APP_FILES_VERSION.toString())
        }
    }

    private fun copyAssetFile(ctx: Context, assetPath: String, dest: File) {
        dest.parentFile?.mkdirs()
        ctx.assets.open(assetPath).use { input ->
            dest.outputStream().use { output -> input.copyTo(output) }
        }
    }

    /** Recursively copy an assets directory into destDir, overwriting files but
     *  never deleting anything already there. */
    private fun copyAssetTree(ctx: Context, assetDir: String, destDir: File) {
        val entries = ctx.assets.list(assetDir) ?: return
        destDir.mkdirs()
        for (name in entries) {
            val childAsset = "$assetDir/$name"
            val grandChildren = ctx.assets.list(childAsset)
            if (grandChildren != null && grandChildren.isNotEmpty()) {
                copyAssetTree(ctx, childAsset, File(destDir, name))
            } else {
                copyAssetFile(ctx, childAsset, File(destDir, name))
            }
        }
    }
}
