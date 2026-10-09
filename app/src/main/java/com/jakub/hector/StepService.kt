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
 * The hardware sensor reports steps-since-boot, so we track a per-day baseline
 * and attribute deltas to the current calendar day, handling reboots (counter
 * resets to ~0) the same way the countdown app does.
 */
class StepService : Service(), SensorEventListener {

    private lateinit var prefs: SharedPreferences
    private var sensorManager: SensorManager? = null
    private var stepSensor: Sensor? = null
    private val dbExecutor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())

    private var lastPushMs = 0L
    private var lastPushedSteps = -1L

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
        val today = LocalDate.now().toString() // ISO yyyy-MM-dd

        val storedDay = prefs.getString(KEY_DAY, null)
        var dayAccum = prefs.getLong(KEY_ACCUM, 0L)
        val lastRaw = prefs.getLong(KEY_LAST_RAW, -1L)

        if (storedDay != today || lastRaw < 0L) {
            // New day (or first ever reading): start counting today from here.
            dayAccum = 0L
        } else {
            val delta = if (raw >= lastRaw) raw - lastRaw else raw // reboot -> reset
            if (delta > 0L) dayAccum += delta
        }

        val walkDelta = if (lastRaw < 0L) 0L else if (raw >= lastRaw) raw - lastRaw else raw

        prefs.edit()
            .putString(KEY_DAY, today)
            .putLong(KEY_ACCUM, dayAccum)
            .putLong(KEY_LAST_RAW, raw)
            .apply()

        updateNotification(dayAccum)
        pushToDb(today, dayAccum)
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
        dbExecutor.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ---- DB bridge ---------------------------------------------------------

    private fun pushToDb(today: String, steps: Long) {
        val now = System.currentTimeMillis()
        if (steps == lastPushedSteps) return
        if (steps != 0L && now - lastPushMs < PUSH_THROTTLE_MS) return
        lastPushMs = now
        lastPushedSteps = steps
        dbExecutor.execute {
            try {
                if (Python.isStarted()) {
                    Python.getInstance()
                        .getModule("mobile_steps")
                        .callAttr("set_today_steps", today, steps)
                }
            } catch (e: Exception) {
                // Ignore; the next sensor update will retry.
            }
        }
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
        val steps = prefs.getLong(KEY_ACCUM, 0L)
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
