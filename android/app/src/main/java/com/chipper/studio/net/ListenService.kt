package com.chipper.studio.net

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.chipper.studio.MainActivity
import com.chipper.studio.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Stays connected to your ntfy topic (the one Chipper on the laptop sends to), so the app gets alerts instantly
 * and always knows the laptop's newest link. This replaces the separate ntfy app.
 */
class ListenService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(100, TimeUnit.SECONDS).build()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        channels(this)
        val n = NotificationCompat.Builder(this, CH_SERVICE)
            .setSmallIcon(R.drawable.ic_notify).setContentTitle("Listening for your laptop")
            .setContentText("Alerts and new links from Chipper arrive here instantly")
            .setContentIntent(openApp(this, 0)).setOngoing(true).setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_MIN).build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE) else startForeground(1, n)
        scope.launch { listen() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() { scope.cancel(); super.onDestroy() }

    private suspend fun listen() {
        val prefs = Prefs(this)
        var wait = 2_000L
        while (scope.isActive) {
            val topic = prefs.ntfyTopic
            if (topic.isEmpty()) { stopSelf(); return }
            val since = prefs.lastMsg.ifEmpty { "10m" }
            try {
                val req = Request.Builder().url("${prefs.ntfyServer}/$topic/json?since=$since").build()
                http.newCall(req).execute().use { r ->
                    if (!r.isSuccessful) throw Exception("ntfy ${r.code}")
                    wait = 2_000L
                    val src = r.body!!.source()
                    while (scope.isActive) {
                        val line = src.readUtf8Line() ?: break
                        if (line.isBlank()) continue
                        val j = runCatching { JSONObject(line) }.getOrNull() ?: continue
                        if (j.optString("event") != "message") continue
                        prefs.lastMsg = j.optString("id")
                        handle(prefs, j)
                    }
                }
            } catch (e: Exception) { /* offline or dropped: try again */ }
            delay(wait); wait = (wait * 2).coerceAtMost(60_000L)
        }
    }

    private fun handle(prefs: Prefs, j: JSONObject) {
        val title = j.optString("title").ifEmpty { "Chipper" }
        val msg = j.optString("message")
        val click = j.optString("click")
        val link = Regex("https://[a-z0-9-]+\\.trycloudflare\\.com").find("$click $msg")?.value
        if (link != null) {
            prefs.base = link; prefs.manualBase = link
            notify(this, CH_LAPTOP, 2, "Laptop online", "Chipper is ready. Tap to open.")
            sendBroadcast(Intent(ACTION_LINK).setPackage(packageName))
            return
        }
        notify(this, CH_CLIPS, (j.optLong("time") % 100000).toInt() + 10, title, msg)
    }

    companion object {
        const val CH_SERVICE = "listen"
        const val CH_LAPTOP = "laptop"
        const val CH_CLIPS = "clips"
        const val ACTION_LINK = "com.chipper.studio.LINK"

        fun start(ctx: Context) {
            if (Prefs(ctx).ntfyTopic.isEmpty() || !Prefs(ctx).listen) return
            runCatching { ContextCompat.startForegroundService(ctx, Intent(ctx, ListenService::class.java)) }
        }
        fun stop(ctx: Context) { ctx.stopService(Intent(ctx, ListenService::class.java)) }

        fun channels(ctx: Context) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CH_SERVICE, "Staying connected", NotificationManager.IMPORTANCE_MIN).apply { setShowBadge(false) })
            nm.createNotificationChannel(NotificationChannel(CH_LAPTOP, "Laptop online", NotificationManager.IMPORTANCE_DEFAULT))
            nm.createNotificationChannel(NotificationChannel(CH_CLIPS, "New clips and posts", NotificationManager.IMPORTANCE_HIGH))
        }

        fun openApp(ctx: Context, req: Int): PendingIntent = PendingIntent.getActivity(
            ctx, req, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

        fun notify(ctx: Context, ch: String, id: Int, title: String, text: String) {
            val n: Notification = NotificationCompat.Builder(ctx, ch).setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(title).setContentText(text).setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setColor(0xFFFF3D6E.toInt()).setAutoCancel(true).setContentIntent(openApp(ctx, id)).build()
            runCatching { ctx.getSystemService(NotificationManager::class.java).notify(id, n) }
        }
    }
}

/** Starts listening again after the phone restarts or the app is updated. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) { ListenService.start(context) }
}
