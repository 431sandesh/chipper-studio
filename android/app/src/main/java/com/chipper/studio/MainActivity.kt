package com.chipper.studio

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import com.chipper.studio.net.Api
import com.chipper.studio.net.Finder
import com.chipper.studio.net.ListenService
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.chipper.studio.net.Prefs
import com.chipper.studio.ui.ChipperTheme
import com.chipper.studio.ui.ConnectScreen
import com.chipper.studio.ui.HomeScreen
import kotlinx.coroutines.launch

enum class Link { CHECKING, ONLINE, OFFLINE }

/** App-wide state: where the laptop is, whether it answers, and whether we are signed in. */
class AppModel(val api: Api) {
    var link by mutableStateOf(Link.CHECKING)
    var signedIn by mutableStateOf(false)
    var note by mutableStateOf("")
    var dark by mutableStateOf(api.prefs.dark)

    /** Last answer for each page, kept between launches, so screens show at once and refresh quietly. */
    val cache = androidx.compose.runtime.mutableStateMapOf<String, org.json.JSONObject>()
    /** When a clip's thumbnail was last changed from the app, so its picture reloads. */
    val coverV = androidx.compose.runtime.mutableStateMapOf<String, Long>()
    fun cached(path: String): org.json.JSONObject? = cache[path] ?: api.prefs.cached(path)?.also { cache[path] = it }
    suspend fun load(path: String): org.json.JSONObject {
        val j = api.get(path)
        cache[path] = j; api.prefs.store(path, j)
        return j
    }

    /** Find the laptop's current link and check that it answers. */
    suspend fun check() {
        link = Link.CHECKING; note = ""
        val p = api.prefs
        val tries = linkedSetOf<String>()
        Finder.latestLink(api)?.let { tries += it }          // newest link Chipper sent to your ntfy topic
        if (p.manualBase.isNotEmpty()) tries += p.manualBase
        if (p.base.isNotEmpty()) tries += p.base
        for (b in tries) {
            if (api.alive(b)) {
                p.base = b; link = Link.ONLINE
                signedIn = api.signedIn()
                return
            }
        }
        link = Link.OFFLINE; signedIn = false
        note = when {
            p.ntfyTopic.isEmpty() && p.manualBase.isEmpty() -> "Add your laptop's link once to get started."
            else -> "Chipper is not answering. Is the laptop on with Chipper running and the remote link switched on?"
        }
    }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val model = AppModel(Api(Prefs(this)))
        ListenService.start(this)
        setContent { Root(model) }
    }
}

@Composable
fun Root(model: AppModel) {
    val scope = rememberCoroutineScope()
    var home by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { model.check() }
    val ctx = LocalContext.current
    // a new link arrived from the laptop: check it straight away
    DisposableEffect(Unit) {
        val r = object : BroadcastReceiver() { override fun onReceive(c: Context, i: Intent) { scope.launch { model.check() } } }
        ContextCompat.registerReceiver(ctx, r, IntentFilter(ListenService.ACTION_LINK), ContextCompat.RECEIVER_NOT_EXPORTED)
        onDispose { ctx.unregisterReceiver(r) }
    }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { if (!home || model.link != Link.ONLINE) scope.launch { model.check() } }
    ChipperTheme(dark = model.dark) {
        if (home && model.signedIn) {
            HomeScreen(model, onSignedOut = { home = false; scope.launch { model.check() } })
        } else {
            ConnectScreen(model, onEnter = { home = true })
        }
    }
    BackHandler(enabled = false) {}
}
