package com.jakub.hector

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.chaquo.python.Python
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var swipeRefresh: SwipeRefreshLayout
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null

    private val fileChooserLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val uris = WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        fileChooserCallback?.onReceiveValue(uris)
        fileChooserCallback = null
    }

    // WebView ignores downloads by default. For a download (the backup export)
    // we ask where to save it, then stream the URL from the local server there.
    private var pendingDownloadUrl: String? = null

    private val saveDocumentLauncher = registerForActivityResult(
        ActivityResultContracts.CreateDocument("application/zip")
    ) { dest ->
        val url = pendingDownloadUrl
        pendingDownloadUrl = null
        if (dest != null && url != null) saveDownload(url, dest)
    }

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {
        maybeStartStepService()
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        swipeRefresh = SwipeRefreshLayout(this)
        swipeRefresh.addView(webView)
        setContentView(swipeRefresh)

        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            builtInZoomControls = true
            displayZoomControls = false
            useWideViewPort = true
            loadWithOverviewMode = true
            allowFileAccess = true
        }

        // Pull down from the top to refresh (like Fitbit and other step apps):
        // reloads the current page, which re-reads today's steps from the DB.
        swipeRefresh.setOnRefreshListener { webView.reload() }
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                swipeRefresh.isRefreshing = false
            }
        }
        webView.setDownloadListener { url, _, contentDisposition, mimeType, _ ->
            pendingDownloadUrl = url
            saveDocumentLauncher.launch(URLUtil.guessFileName(url, contentDisposition, mimeType))
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView?,
                callback: ValueCallback<Array<Uri>>?,
                params: FileChooserParams?
            ): Boolean {
                fileChooserCallback?.onReceiveValue(null)
                fileChooserCallback = callback
                return try {
                    fileChooserLauncher.launch(params?.createIntent())
                    true
                } catch (e: Exception) {
                    fileChooserCallback = null
                    false
                }
            }
        }

        ensurePermissions()

        // Start the Flask server off the main thread, then load it. make_server
        // binds synchronously, so by the time callAttr returns the port is live.
        Thread {
            Python.getInstance()
                .getModule("mobile_main")
                .callAttr("start_server", HectorApp.PORT)
            // Reopen the page that was showing if Android recreated us.
            val restore = savedInstanceState?.getString(KEY_URL)
                ?.takeIf { it.startsWith(HectorApp.BASE_URL) }
            runOnUiThread { webView.loadUrl(restore ?: HectorApp.BASE_URL) }
        }.start()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.url?.let { outState.putString(KEY_URL, it) }
    }

    override fun onResume() {
        super.onResume()
        maybeStartStepService()
    }

    @Suppress("DEPRECATION")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    private fun saveDownload(url: String, dest: Uri) {
        Thread {
            val ok = try {
                val conn = URL(url).openConnection() as HttpURLConnection
                try {
                    conn.inputStream.use { input ->
                        contentResolver.openOutputStream(dest)!!.use { output -> input.copyTo(output) }
                    }
                } finally {
                    conn.disconnect()
                }
                true
            } catch (e: Exception) {
                false
            }
            runOnUiThread {
                Toast.makeText(
                    this,
                    if (ok) "Backup saved" else "Saving the backup failed",
                    Toast.LENGTH_LONG
                ).show()
            }
        }.start()
    }

    // ---- Permissions & step service ---------------------------------------

    private fun ensurePermissions() {
        val needed = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
            !hasPermission(Manifest.permission.ACTIVITY_RECOGNITION)
        ) {
            needed.add(Manifest.permission.ACTIVITY_RECOGNITION)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            !hasPermission(Manifest.permission.POST_NOTIFICATIONS)
        ) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (needed.isNotEmpty()) {
            permissionLauncher.launch(needed.toTypedArray())
        } else {
            maybeStartStepService()
        }
    }

    private fun hasPermission(permission: String): Boolean =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun maybeStartStepService() {
        val ok = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
            hasPermission(Manifest.permission.ACTIVITY_RECOGNITION)
        if (ok) StepService.start(this)
    }

    companion object {
        private const val KEY_URL = "webview_url"
    }
}
