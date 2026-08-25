package com.jakub.hector

import android.content.Context
import java.io.File

/**
 * Installs the bundled data into writable storage.
 *
 * Three independent version markers so updates never destroy user data:
 *  - **Database** (`.db_version`): copied when missing, or when DB_SEED_VERSION
 *    increases. Bump DB_SEED_VERSION only when you deliberately want to replace
 *    the on-device database with the freshly bundled one (this discards any data
 *    entered on the phone), e.g. to recover from a bad/empty DB.
 *  - **App files** (`.appfiles_version`): templates/ and static/ are shipped
 *    code, re-copied when APP_FILES_VERSION increases. Copying overwrites the
 *    bundled files but never deletes extras, so user-uploaded ingredient photos
 *    are preserved.
 */
object SeedInstaller {

    private const val DB_SEED_VERSION = 1
    private const val APP_FILES_VERSION = 5

    fun ensureSeed(ctx: Context, dataDir: File) {
        dataDir.mkdirs()

        // 1. Database.
        val db = File(dataDir, "hector.db")
        val dbMarker = File(dataDir, ".db_version")
        val dbInstalled = when {
            dbMarker.exists() -> dbMarker.readText().trim().toIntOrNull() ?: 1
            db.exists() -> 1   // seeded by an older build that had no db marker
            else -> 0
        }
        if (!db.exists() || dbInstalled < DB_SEED_VERSION) {
            copyAssetFile(ctx, "seed/hector.db", db)
            dbMarker.writeText(DB_SEED_VERSION.toString())
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
