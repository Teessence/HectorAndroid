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
import java.util.concurrent.Executors

/**
 * Reads the hardware step counter and keeps *today's* step total in Hector's
 * database up to date (source='android'), replacing the old Garmin sync.
 *
 * The hardware sensor reports steps-since-boot. Each reading is handed to the
 * database (mobile_steps.record_reading), which keeps the previous reading and
 * adds the difference to today — so the database, and any backup of it, is the
 * single source of truth for the count.
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
        createChannel()
        startForegroundInternal()

        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        stepSensor = sensorManager?.getDefaultSensor(Sensor.TYPE_STEP_COUNTER)
        stepSensor?.let { sensor ->
            sensorManager?.registerListener(this, sensor, SensorManager.SENSOR_DELAY_NORMAL)
        }
        // Close out a walk left open if the service was restarted mid-walk.
        handler.post(walkCheck)
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

        latestRaw = raw
        latestDay = LocalDate.now().toString() // ISO yyyy-MM-dd
        sendReading(force = false)

        // Walk detection only needs the local step-by-step difference.
        val walkDelta = if (lastRaw < 0L) 0L else if (raw >= lastRaw) raw - lastRaw else raw
        if (walkDelta > 0L) trackWalk(eventWallTime(event), walkDelta)
    }

    // ---- Walk detection ----------------------------------------------------
    // A walk is a run of steps with no pause longer than WALK_GAP_MS. Once it
    // ends, it's logged if it lasted at least WALK_MIN_MS with a real walking
    // pace. This sits on top of the daily count and never changes it.

    /** Sensor timestamps are nanos since boot; convert to wall-clock millis so
     *  batched (delayed) events still land at the time the steps happened. */
    private fun eventWallTime(event: SensorEvent): Long {
        val now = System.currentTimeMillis()
        val ageMs = (SystemClock.elapsedRealtimeNanos() - event.timestamp) / 1_000_000L
        return if (ageMs in 0L..WALK_GAP_MS * 30) now - ageMs else now
    }

    private fun trackWalk(at: Long, steps: Long) {
        val start = prefs.getLong(KEY_WALK_START, 0L)
        val last = prefs.getLong(KEY_WALK_LAST, 0L)
        if (start == 0L || at - last > WALK_GAP_MS) {
            finishWalk()
            prefs.edit()
                .putLong(KEY_WALK_START, at)
                .putLong(KEY_WALK_LAST, at)
                .putLong(KEY_WALK_STEPS, steps)
                .apply()
        } else {
            prefs.edit()
                .putLong(KEY_WALK_LAST, maxOf(last, at))
                .putLong(KEY_WALK_STEPS, prefs.getLong(KEY_WALK_STEPS, 0L) + steps)
                .apply()
        }
        scheduleWalkCheck()
    }

    /** Close the current walk (if any) and log it when it qualifies. */
    private fun finishWalk() {
        val start = prefs.getLong(KEY_WALK_START, 0L)
        if (start == 0L) return
        val end = prefs.getLong(KEY_WALK_LAST, start)
        val steps = prefs.getLong(KEY_WALK_STEPS, 0L)
        prefs.edit().remove(KEY_WALK_START).remove(KEY_WALK_LAST).remove(KEY_WALK_STEPS).apply()
        val minutes = (end - start) / 60_000.0
        if (end - start >= WALK_MIN_MS && steps / minutes >= WALK_MIN_PACE) {
            logWalk(start, end, steps)
        }
    }

    private val walkCheck = Runnable {
        val last = prefs.getLong(KEY_WALK_LAST, 0L)
        if (last != 0L && System.currentTimeMillis() - last > WALK_GAP_MS) finishWalk()
        else if (last != 0L) scheduleWalkCheck()
    }

    private fun scheduleWalkCheck() {
        handler.removeCallbacks(walkCheck)
        handler.postDelayed(walkCheck, WALK_GAP_MS + 5_000L)
    }

    private fun logWalk(start: Long, end: Long, steps: Long) {
        val fmt = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
        val s = fmt.format(java.util.Date(start))
        val e = fmt.format(java.util.Date(end))
        dbExecutor.execute {
            try {
                if (Python.isStarted()) {
                    Python.getInstance().getModule("mobile_steps").callAttr("log_walk", s, e, steps)
                }
            } catch (ex: Exception) {
                // A lost walk entry is not worth crashing the counter over.
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        sensorManager?.unregisterListener(this)
        handler.removeCallbacks(walkCheck)
        handler.removeCallbacks(sendLater)
        sendReading(force = true)  // queued before the executor shuts down below
        dbExecutor.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ---- DB bridge ---------------------------------------------------------
    // The service sends the raw counter reading (steps since boot) and the
    // database works out how many steps are new since the reading it stored
    // last. That stored reading travels inside backups, so after uninstall →
    // install → import the next reading picks up every step walked since the
    // export. Readings are absolute, so a skipped or failed send loses nothing.

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
        dbExecutor.execute {
            try {
                if (Python.isStarted()) {
                    val total = Python.getInstance()
                        .getModule("mobile_steps")
                        .callAttr("record_reading", day, raw, boot)
                        .toLong()
                    handler.post {
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
        private const val KEY_DAY = "day"
        private const val KEY_ACCUM = "day_accum"
        private const val KEY_LAST_RAW = "last_raw"
        private const val KEY_WALK_START = "walk_start"
        private const val KEY_WALK_LAST = "walk_last"
        private const val KEY_WALK_STEPS = "walk_steps"
        private const val WALK_GAP_MS = 2 * 60_000L      // a pause longer than this ends a walk
        private const val WALK_MIN_MS = 5 * 60_000L      // shortest walk worth logging
        private const val WALK_MIN_PACE = 40.0           // steps/min; filters pottering about

        fun start(context: Context) {
            ContextCompat.startForegroundService(
                context, Intent(context, StepService::class.java)
            )
        }
    }
}
