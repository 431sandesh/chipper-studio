package com.chipper.studio.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.VideoLibrary
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chipper.studio.AppModel
import com.chipper.studio.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Shows a short message at the bottom of the screen from anywhere in the app. */
class Toaster(private val host: SnackbarHostState, private val scope: CoroutineScope) {
    fun show(msg: String) { scope.launch { host.currentSnackbarData?.dismiss(); host.showSnackbar(msg) } }
}
val LocalToast = staticCompositionLocalOf<Toaster> { error("no toaster") }

enum class Tab(val label: String) { LIVE("Live"), VIDEOS("Videos"), AUTO("Auto-clip"), CLIPS("Clips"), SETTINGS("Settings") }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(model: AppModel, onSignedOut: () -> Unit) {
    val host = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val toaster = remember { Toaster(host, scope) }
    var tab by remember { mutableStateOf(Tab.CLIPS) }
    var open by remember { mutableStateOf<JSONObject?>(null) }       // clip being viewed
    var editing by remember { mutableStateOf<String?>(null) }         // clip in the editor
    var cover by remember { mutableStateOf(false) }                  // thumbnail editor instead
    var native by remember { mutableStateOf<String?>(null) }          // clip in the native editor
    var thumbFor by remember { mutableStateOf<String?>(null) }        // clip in the thumbnail editor

    // alerts from the laptop need notification permission on Android 13+
    val notifAsk = androidx.activity.compose.rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.RequestPermission()) { }
    androidx.compose.runtime.LaunchedEffect(Unit) {
        if (android.os.Build.VERSION.SDK_INT >= 33) notifAsk.launch(android.Manifest.permission.POST_NOTIFICATIONS)
    }
    CompositionLocalProvider(LocalToast provides toaster) {
        Box(Modifier.fillMaxSize()) {
            Scaffold(
                topBar = {
                    CenterAlignedTopAppBar(
                        title = {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Image(painterResource(R.drawable.logo), "Chipper Studio", Modifier.height(30.dp))
                                Spacer(Modifier.width(10.dp))
                                StatusDot(model.link, 9)
                            }
                        },
                        colors = TopAppBarDefaults.centerAlignedTopAppBarColors(containerColor = MaterialTheme.colorScheme.background),
                    )
                },
                bottomBar = {
                    NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                        Tab.entries.forEach { t ->
                            NavigationBarItem(
                                selected = tab == t, onClick = { tab = t },
                                icon = {
                                    Icon(when (t) { Tab.LIVE -> Icons.Default.Sensors; Tab.VIDEOS -> Icons.Default.Movie; Tab.AUTO -> Icons.Default.Visibility; Tab.CLIPS -> Icons.Default.VideoLibrary; Tab.SETTINGS -> Icons.Default.Settings }, t.label)
                                },
                                label = { Text(t.label, maxLines = 1, fontSize = 11.sp) },
                                colors = NavigationBarItemDefaults.colors(indicatorColor = Pink.copy(alpha = 0.14f), selectedIconColor = PinkDeep, selectedTextColor = PinkDeep),
                            )
                        }
                    }
                },
                snackbarHost = { SnackbarHost(host) },
                containerColor = MaterialTheme.colorScheme.background,
            ) { pad ->
                Box(Modifier.padding(pad).fillMaxSize()) {
                    when (tab) {
                        Tab.LIVE -> LiveScreen(model)
                        Tab.VIDEOS -> VideosScreen(model)
                        Tab.AUTO -> AutoClipScreen(model)
                        Tab.CLIPS -> ClipsScreen(model, onOpen = { open = it })
                        Tab.SETTINGS -> SettingsScreen(model, onSignOut = { model.api.prefs.cookie = ""; model.signedIn = false; onSignedOut() })
                    }
                }
            }
            open?.let { c ->
                BackHandler { open = null }
                ClipScreen(model, c, onClose = { open = null }, onEdit = { native = c.optString("name") },
                    onThumb = { thumbFor = c.optString("name") })
            }
            native?.let { name ->
                com.chipper.studio.editor.EditorScreen(model, name, onClose = { native = null }, onWeb = { native = null; cover = false; editing = name })
            }
            thumbFor?.let { name ->
                com.chipper.studio.editor.CoverEditorScreen(model, name, onClose = { thumbFor = null })
            }
            editing?.let { name ->
                BackHandler { editing = null }
                WebEditor(model, name, cover = cover, onClose = { editing = null })
            }
        }
    }
}
