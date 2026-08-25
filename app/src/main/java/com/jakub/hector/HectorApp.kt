package com.jakub.hector

import android.app.Application
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/**
 * Sets up the app on launch:
 *  1. Copies the bundled Hector data (DB, templates, static files) into
 *     writable storage on first run.
 *  2. Starts the embedded Python interpreter and points Hector at that storage.
 *
 * The Flask web server itself is started later by MainActivity.
 */
class HectorApp : Application() {

    companion object {
        const val PORT = 8765
        const val BASE_URL = "http://127.0.0.1:$PORT/"
        lateinit var dataDir: File
            private set
    }

    override fun onCreate() {
        super.onCreate()
        dataDir = File(filesDir, "hector_data")
        SeedInstaller.ensureSeed(this, dataDir)

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        Python.getInstance()
            .getModule("mobile_main")
            .callAttr("configure", dataDir.absolutePath)
    }
}
