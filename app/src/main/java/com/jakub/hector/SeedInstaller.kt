package com.jakub.hector

import android.content.Context
import java.io.File

/**
 * Copies the bundled seed data (assets/seed/**) into writable storage the first
 * time the app runs. The seed contains hector.db plus the templates/ and
 * static/ folders Flask serves. Bump SEED_VERSION to force a re-copy after a
 * bundled-data change (this overwrites the DB, so only bump when intended).
 */
object SeedInstaller {

    private const val SEED_VERSION = 1
    private const val SEED_ROOT = "seed"

    fun ensureSeed(ctx: Context, dataDir: File) {
        val marker = File(dataDir, ".seed_version")
        val installed = if (marker.exists()) marker.readText().trim().toIntOrNull() ?: 0 else 0
        val dbExists = File(dataDir, "hector.db").exists()
        if (installed >= SEED_VERSION && dbExists) return

        dataDir.mkdirs()
        copyAssetTree(ctx, SEED_ROOT, dataDir)
        marker.writeText(SEED_VERSION.toString())
    }

    /** Recursively copy an assets directory into destDir. */
    private fun copyAssetTree(ctx: Context, assetDir: String, destDir: File) {
        val entries = ctx.assets.list(assetDir) ?: return
        destDir.mkdirs()
        for (name in entries) {
            val childAsset = "$assetDir/$name"
            val grandChildren = ctx.assets.list(childAsset)
            if (grandChildren != null && grandChildren.isNotEmpty()) {
                copyAssetTree(ctx, childAsset, File(destDir, name))
            } else {
                // A leaf: AssetManager.list() returns an empty array for files.
                ctx.assets.open(childAsset).use { input ->
                    File(destDir, name).outputStream().use { output ->
                        input.copyTo(output)
                    }
                }
            }
        }
    }
}
