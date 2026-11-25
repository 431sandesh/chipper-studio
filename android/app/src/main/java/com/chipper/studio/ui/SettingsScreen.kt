package com.chipper.studio.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.chipper.studio.AppModel
import kotlinx.coroutines.launch
import androidx.compose.foundation.clickable
import com.chipper.studio.net.objects
import org.json.JSONObject

private sealed class S(val key: String, val label: String, val hint: String = "") {
    class Bool(k: String, l: String, h: String = "") : S(k, l, h)
    class Num(k: String, l: String, val min: Float, val max: Float, val step: Float, val unit: String = "", h: String = "") : S(k, l, h)
    class Choice(k: String, l: String, val opts: List<Pair<String, String>>, h: String = "") : S(k, l, h)
    class Txt(k: String, l: String, h: String = "") : S(k, l, h)
}

private val GROUPS = listOf(
    "Finding clips" to listOf(
        S.Num("ratio", "Chat spike needed", 1.5f, 6f, 0.1f, "×", "How much busier chat must get than usual"),
        S.Num("min", "Fewest messages for a spike", 2f, 40f, 1f),
        S.Num("pre", "Seconds before the moment", 5f, 60f, 1f, " s"),
        S.Num("post", "Seconds after the moment", 3f, 40f, 1f, " s"),
        S.Num("cooldown", "Wait between clips", 0f, 180f, 5f, " s"),
        S.Num("max_len", "Longest clip", 15f, 180f, 5f, " s"),
        S.Bool("auto_extend", "Keep going while chat stays busy"),
        S.Bool("smart_trim", "Trim to full sentences"),
        S.Bool("use_ratings", "Learn from my ratings"),
    ),
    "Quality" to listOf(
        S.Choice("rec_quality", "Recording quality", listOf("best" to "Best (1080p60)", "1080" to "1080p", "720" to "720p", "480" to "480p")),
        S.Choice("render_quality", "Final clip quality", listOf("standard" to "Standard", "high" to "High", "max" to "Max")),
        S.Bool("enhance_default", "AI-enhance every new clip"),
        S.Choice("enhance_quality", "Enhance quality", listOf("MEDIUM" to "Medium", "HIGH" to "High", "ULTRA" to "Ultra")),
        S.Choice("enhance_sharpen", "Sharpen after enhancing", listOf("off" to "Off", "light" to "Light", "medium" to "Medium", "strong" to "Strong")),
    ),
    "Captions and posts" to listOf(
        S.Bool("captions", "Add captions"),
        S.Bool("yt_captions", "Use YouTube's captions when there are any"),
        S.Txt("hashtags", "Default hashtags"),
        S.Txt("avoid_words", "Words to avoid", "Clips that say these get a warning before posting"),
        S.Num("post_daily_cap", "Most posts per account per day", 0f, 30f, 1f, "", "0 means no limit"),
        S.Num("post_gap_min", "Minutes between posts on one account", 0f, 240f, 5f, " min"),
    ),
    "Laptop" to listOf(
        S.Bool("keep_recordings", "Keep live recordings"),
        S.Bool("notify_clips", "Notify my phone about new clips"),
    ),
)

@Composable
fun SettingsScreen(model: AppModel, onSignOut: () -> Unit) {
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var cfg by remember { mutableStateOf(model.cached("/api/config")?.optJSONObject("settings")) }
    var askRestart by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { runCatching { model.load("/api/config") }.onSuccess { cfg = it.optJSONObject("settings") }.onFailure { if (cfg == null) toast.show(it.message ?: "") } }
    fun set(k: String, v: Any) {
        cfg?.put(k, v)
        scope.launch { runCatching { model.api.post("/api/settings", JSONObject().put(k, v)) }.onFailure { toast.show(it.message ?: "Could not save") } }
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("Settings", style = MaterialTheme.typography.titleLarge)
        val c = cfg
        if (c == null) repeat(3) { CardSkeleton(4) }
        else GROUPS.forEach { (title, items) ->
            Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(title, style = MaterialTheme.typography.titleMedium)
                    items.filter { c.has(it.key) }.forEachIndexed { i, s ->
                        if (i > 0) HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.5f))
                        SettingRow(s, c) { v -> set(s.key, v) }
                    }
                }
            }
        }
        UploadsCard(model)
        Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("This app", style = MaterialTheme.typography.titleMedium)
                val ctx = androidx.compose.ui.platform.LocalContext.current
                var listen by remember { mutableStateOf(model.api.prefs.listen) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Instant alerts from the laptop")
                        Text("New clips, posts and the laptop's new link. Replaces the ntfy app.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Switch(listen, {
                        listen = it; model.api.prefs.listen = it
                        if (it) com.chipper.studio.net.ListenService.start(ctx) else com.chipper.studio.net.ListenService.stop(ctx)
                    })
                }
                OutlinedButton(onClick = {
                    runCatching {
                        ctx.startActivity(android.content.Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, android.net.Uri.parse("package:" + ctx.packageName)))
                    }
                }, Modifier.fillMaxWidth()) { Text("Keep alerts working when the phone sleeps") }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Dark mode", Modifier.weight(1f))
                    Switch(model.dark, { model.dark = it; model.api.prefs.dark = it })
                }
                Text("Connected to ${model.api.base.removePrefix("https://")}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("Keys, social account logins and the remote password can only be changed on the laptop.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    OutlinedButton(onClick = { askRestart = true }, Modifier.weight(1f)) { Text("Restart Chipper") }
                    OutlinedButton(onClick = onSignOut, Modifier.weight(1f)) { Text("Sign out") }
                }
            }
        }
        Spacer(Modifier.height(20.dp))
    }
    if (askRestart) AlertDialog(
        onDismissRequest = { askRestart = false },
        title = { Text("Restart Chipper on the laptop?") },
        text = { Text("Any stream being recorded stops, and clips still being made are lost. The remote link may change; the app finds the new one by itself.") },
        confirmButton = { TextButton(onClick = { askRestart = false; scope.launch { runCatching { model.api.post("/api/restart") }; toast.show("Restarting… reconnect in a minute") } }) { Text("Restart", color = Bad) } },
        dismissButton = { TextButton(onClick = { askRestart = false }) { Text("Cancel") } },
    )
}

@Composable
private fun SettingRow(s: S, c: JSONObject, save: (Any) -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        when (s) {
            is S.Bool -> {
                var v by remember { mutableStateOf(c.optBoolean(s.key)) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(s.label, Modifier.weight(1f))
                    Switch(v, { v = it; save(it) })
                }
            }
            is S.Num -> {
                var v by remember { mutableStateOf(c.optDouble(s.key, s.min.toDouble()).toFloat()) }
                Row { Text(s.label, Modifier.weight(1f)); Text((if (s.step >= 1f) v.toInt().toString() else "%.1f".format(v)) + s.unit, style = MaterialTheme.typography.labelLarge) }
                Slider(v, { v = (Math.round(it / s.step) * s.step).coerceIn(s.min, s.max) }, valueRange = s.min..s.max,
                    onValueChangeFinished = { save(if (s.step >= 1f) v.toInt() else v.toDouble()) })
            }
            is S.Choice -> {
                var v by remember { mutableStateOf(c.optString(s.key)) }
                Text(s.label)
                Picker("", s.opts.firstOrNull { it.first == v }?.second ?: v, s.opts.map { it.second }, Modifier.fillMaxWidth().padding(top = 6.dp)) { lab ->
                    v = s.opts.first { it.second == lab }.first; save(v)
                }
            }
            is S.Txt -> {
                var v by remember { mutableStateOf(c.optString(s.key)) }
                OutlinedTextField(v, { v = it }, Modifier.fillMaxWidth(), label = { Text(s.label) })
                TextButton(onClick = { save(v) }) { Text("Save") }
            }
        }
        if (s.hint.isNotEmpty()) Text(s.hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}


/** What's been uploaded, kept on the PC in uploads\\audio, fx, images and other. */
@Composable
private fun UploadsCard(model: AppModel) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val toast = LocalToast.current
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var data by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(model.cached("/api/uploads")) }
    var open by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf<String?>(null) }
    suspend fun reload() { runCatching { model.load("/api/uploads") }.onSuccess { data = it } }
    androidx.compose.runtime.LaunchedEffect(Unit) { reload() }
    val upload = com.chipper.studio.net.rememberUploader(model, "*/*") { scope.launch { reload() } }
    val names = linkedMapOf("audio" to "Music", "fx" to "Sound effects", "images" to "Images", "other" to "Other files")
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Files on your PC", style = MaterialTheme.typography.titleMedium)
            Text("Everything you upload from the phone is kept on the laptop in the uploads folder, sorted into audio, fx, images and other.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            data?.optString("path")?.takeIf { it.isNotEmpty() }?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            val f = data?.optJSONObject("folders")
            if (f == null) CardSkeleton(2)
            else names.forEach { (k, label) ->
                val files = f.optJSONArray(k)?.objects() ?: emptyList()
                androidx.compose.foundation.layout.Row(Modifier.fillMaxWidth().clickable { open = if (open == k) null else k }.padding(vertical = 6.dp), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    Text(label, Modifier.weight(1f))
                    Text("${files.size} file${if (files.size == 1) "" else "s"}", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                }
                if (open == k) files.take(40).forEach { x ->
                    androidx.compose.foundation.layout.Row(Modifier.fillMaxWidth().padding(start = 12.dp), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        Text(x.optString("file"), Modifier.weight(1f), style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                        Text("${x.optLong("size") / 1024} KB", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            OutlinedButton(onClick = upload, Modifier.fillMaxWidth()) { Text("Upload files") }
        }
    }
}
