package com.jakub.hector

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import java.time.LocalDate
import java.util.TimeZone
import java.util.concurrent.Executors

/**
 * Reads the hardware step counter and keeps *today's* step total in Hector's
 * database up to date (source='android'), replacing the old Garmin sync.
 *
 * The hardware sensor reports steps-since-boot. Each reading is handed to the
 * database (mobile_steps.record_reading), which keeps the previous reading and
 * adds the difference to today — so the database, and any backup of it, is the
 * single source of truth for the count.
 *
 * Alongside, steps are tallied per minute of the day they were taken in. The
 * app builds walks from those minutes using the user's walk settings, so the
 * walk rules can change at any time and past walks regroup accordingly.
 */
class StepService : Service(), SensorEventListener {

    private lateinit var prefs: SharedPreferences
    private var sensorManager: SensorManager? = null
    private var stepSensor: Sensor? = null
    private val dbExecutor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())

    private var lastPushMs = 0L

    override fun onCreate() {
        super.onCreate()
        prefs = getSharedPreferences("hector_steps", Context.MODE_PRIVATE)
        // Leftovers from the old in-service walk detection.
        prefs.edit().remove("walk_start").remove("walk_last").remove("walk_steps").apply()
        loadPendingMinutes()
        createChannel()
        startForegroundInternal()

        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        stepSensor = sensorManager?.getDefaultSensor(Sensor.TYPE_STEP_COUNTER)
        stepSensor?.let { sensor ->
            sensorManager?.registerListener(this, sensor, SensorManager.SENSOR_DELAY_NORMAL)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundInternal()
        return START_STICKY
    }

    override fun onSensorChanged(event: SensorEvent) {
        if (event.sensor.type != Sensor.TYPE_STEP_COUNTER) return
        val raw = event.values[0].toLong()
        val lastRaw = prefs.getLong(KEY_LAST_RAW, -1L)
        prefs.edit().putLong(KEY_LAST_RAW, raw).apply()

        // Steps since the previous reading, filed under the minute they happened.
        val delta = if (lastRaw < 0L) 0L else if (raw >= lastRaw) raw - lastRaw else raw
        if (delta > 0L) {
            val at = eventWallTime(event)
            val minute = (at + TimeZone.getDefault().getOffset(at)) / 60_000L  // local-time minute
            pendingMinutes[minute] = (pendingMinutes[minute] ?: 0L) + delta
            lastStepMs = maxOf(lastStepMs, at)
            savePendingMinutes()
        }

        latestRaw = raw
        latestDay = LocalDate.now().toString() // ISO yyyy-MM-dd
        sendReading(force = false)
    }

    /** Sensor timestamps are nanos since boot; convert to wall-clock millis so
     *  batched (delayed) events still land at the time the steps happened. */
    private fun eventWallTime(event: SensorEvent): Long {
        val now = System.currentTimeMillis()
        val ageMs = (SystemClock.elapsedRealtimeNanos() - event.timestamp) / 1_000_000L
        return if (ageMs in 0L..MAX_EVENT_AGE_MS) now - ageMs else now
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        sensorManager?.unregisterListener(this)
        handler.removeCallbacks(sendLater)
        sendReading(force = true)  // queued before the executor shuts down below
        dbExecutor.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ---- Per-minute step log -------------------------------------------------
    // Minutes not yet saved to the database, as local-time minute -> steps.
    // Kept in prefs too, so a restart before the next save loses nothing.

    private val pendingMinutes = mutableMapOf<Long, Long>()
    private var lastStepMs = 0L

    private fun loadPendingMinutes() {
        pendingMinutes.clear()
        prefs.getString(KEY_PENDING_MINUTES, "")!!.split(',').forEach { pair ->
            val parts = pair.split(':')
            if (parts.size == 2) {
                val m = parts[0].toLongOrNull()
                val s = parts[1].toLongOrNull()
                if (m != null && s != null && s > 0) pendingMinutes[m] = s
            }
        }
        lastStepMs = prefs.getLong(KEY_LAST_STEP_MS, 0L)
    }

    private fun savePendingMinutes() {
        prefs.edit()
            .putString(KEY_PENDING_MINUTES, pendingMinutes.entries.joinToString(",") { "${it.key}:${it.value}" })
            .putLong(KEY_LAST_STEP_MS, lastStepMs)
            .apply()
    }

    // ---- DB bridge ---------------------------------------------------------
    // The service sends the raw counter reading (steps since boot) and the
    // database works out how many steps are new since the reading it stored
    // last. That stored reading travels inside backups, so after uninstall →
    // install → import the next reading picks up every step walked since the
    // export. Readings are absolute, so a skipped or failed send loses nothing.
    // The per-minute tallies ride along and are cleared only once saved.

    private var latestRaw = -1L
    private var latestDay = ""
    private val sendLater = Runnable { sendReading(force = true) }

    private fun sendReading(force: Boolean) {
        if (latestRaw < 0L) return
        val now = System.currentTimeMillis()
        if (!force && now - lastPushMs < PUSH_THROTTLE_MS) {
            handler.removeCallbacks(sendLater)
            handler.postDelayed(sendLater, PUSH_THROTTLE_MS)
            return
        }
        lastPushMs = now
        val raw = latestRaw
        val day = latestDay
        val boot = bootCount()
        val minutes = pendingMinutes.toMap()
        val minutesArg = minutes.entries.joinToString(",") { "${it.key}:${it.value}" }
        val lastStep = lastStepMs
        dbExecutor.execute {
            try {
                if (Python.isStarted()) {
                    val total = Python.getInstance()
                        .getModule("mobile_steps")
                        .callAttr("record_reading", day, raw, boot, minutesArg, lastStep)
                        .toLong()
                    handler.post {
                        // Drop exactly what was saved; steps added meanwhile stay pending.
                        minutes.forEach { (m, s) ->
                            val left = (pendingMinutes[m] ?: 0L) - s
                            if (left > 0L) pendingMinutes[m] = left else pendingMinutes.remove(m)
                        }
                        savePendingMinutes()
                        prefs.edit().putString(KEY_DAY, day).putLong(KEY_ACCUM, total).apply()
                        updateNotification(total)
                    }
                }
            } catch (e: Exception) {
                // Ignore; the next reading carries the same information.
            }
        }
    }

    /** Increments on every reboot; tells the DB whether the counter restarted. */
    private fun bootCount(): Int =
        try {
            Settings.Global.getInt(contentResolver, Settings.Global.BOOT_COUNT)
        } catch (e: Exception) {
            -1
        }

    // ---- Notification ------------------------------------------------------

    private fun createChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Step counter",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            setShowBadge(false)
            description = "Counts your steps for Hector."
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun buildNotification(steps: Long): Notification {
        val contentIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Hector")
            .setContentText(String.format("%,d steps today", steps))
            .setSmallIcon(R.drawable.ic_stat_steps)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(contentIntent)
            .build()
    }

    private fun startForegroundInternal() {
        val steps = if (prefs.getString(KEY_DAY, null) == LocalDate.now().toString()) prefs.getLong(KEY_ACCUM, 0L) else 0L
        val type = if (Build.VERSION.SDK_INT >= 34) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH
        } else {
            0
        }
        try {
            ServiceCompat.startForeground(this, NOTIF_ID, buildNotification(steps), type)
        } catch (e: Exception) {
            stopSelf()
        }
    }

    private fun updateNotification(steps: Long) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIF_ID, buildNotification(steps))
    }

    companion object {
        private const val CHANNEL_ID = "hector_steps"
        private const val NOTIF_ID = 1
        private const val PUSH_THROTTLE_MS = 5000L
        private const val MAX_EVENT_AGE_MS = 60 * 60_000L  // older event timestamps aren't trusted
        private const val KEY_DAY = "day"
        private const val KEY_ACCUM = "day_accum"
        private const val KEY_LAST_RAW = "last_raw"
        private const val KEY_PENDING_MINUTES = "pending_minutes"
        private const val KEY_LAST_STEP_MS = "last_step_ms"

        fun start(context: Context) {
            ContextCompat.startForegroundService(
                context, Intent(context, StepService::class.java)
            )
        }
    }
}
