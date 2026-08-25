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
import android.os.IBinder
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

        prefs.edit()
            .putString(KEY_DAY, today)
            .putLong(KEY_ACCUM, dayAccum)
            .putLong(KEY_LAST_RAW, raw)
            .apply()

        updateNotification(dayAccum)
        pushToDb(today, dayAccum)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        sensorManager?.unregisterListener(this)
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

        fun start(context: Context) {
            ContextCompat.startForegroundService(
                context, Intent(context, StepService::class.java)
            )
        }
    }
}
