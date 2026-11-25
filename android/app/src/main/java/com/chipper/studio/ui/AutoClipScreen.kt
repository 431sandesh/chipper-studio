package com.chipper.studio.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ContentCut
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chipper.studio.AppModel
import com.chipper.studio.net.objects
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Channels Chipper watches: when one goes live it records and clips it; new long uploads get clipped too. */
@Composable
fun AutoClipScreen(model: AppModel) {
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var w by remember { mutableStateOf(model.cached("/api/watch")) }
    var add by remember { mutableStateOf("") }
    var adding by remember { mutableStateOf(false) }
    val templates = rememberTemplates(model)
    LaunchedEffect(Unit) {
        while (true) { runCatching { model.load("/api/watch") }.onSuccess { w = it }; delay(3000) }
    }
    fun send(path: String, body: JSONObject, done: String? = null) {
        scope.launch {
            runCatching { model.api.post(path, body) }.onSuccess { w = it; model.cache["/api/watch"] = it; done?.let(toast::show) }.onFailure { toast.show(it.message ?: "Failed") }
        }
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("Auto-clip", style = MaterialTheme.typography.titleLarge)
        Text("Channels Chipper watches for you. When one goes live it starts clipping; when one uploads a long video it clips that too.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        val d = w
        if (d == null) { CardSkeleton(2); CardSkeleton(3); CardSkeleton(3); return@Column }
        Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(if (d.optBoolean("enabled")) "Auto-clip is on" else "Auto-clip is paused", style = MaterialTheme.typography.titleMedium)
                        val next = d.optDouble("next_check", 0.0) - System.currentTimeMillis() / 1000.0
                        Text(when {
                            !d.optBoolean("enabled") -> "Switch on to start watching"
                            d.optString("checking").isNotEmpty() -> "Checking ${d.optString("checking")}…"
                            next > 0 -> "Next check in ${(next / 60).toInt()}:${"%02d".format((next % 60).toInt())}"
                            else -> ""
                        }, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = { send("/api/watch/check", JSONObject(), "Checking your channels…") }) { Icon(Icons.Default.Refresh, "Check now") }
                    Switch(d.optBoolean("enabled"), { send("/api/watch", JSONObject().put("enabled", it), if (it) "Auto-clip is on" else "Auto-clip paused") })
                }
                OutlinedTextField(add, { add = it }, Modifier.fillMaxWidth(), singleLine = true, label = { Text("YouTube @handle, Twitch name or TikTok link") })
                Button(onClick = {
                    adding = true
                    scope.launch {
                        runCatching { model.api.post("/api/watch/add", JSONObject().put("channel", add.trim())) }
                            .onSuccess { w = it; add = ""; toast.show("Channel added") }.onFailure { toast.show(it.message ?: "Could not add") }
                        adding = false
                    }
                }, enabled = add.isNotBlank() && !adding, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) {
                    Text(if (adding) "Finding channel…" else "＋ Add channel", fontWeight = FontWeight.Bold)
                }
            }
        }
        val chans = d.optJSONArray("channels")?.objects() ?: emptyList()
        if (chans.isEmpty()) Text("No channels yet. Add the channels you clip, then switch Auto-clip on.", color = MaterialTheme.colorScheme.onSurfaceVariant)
        chans.forEach { c -> ChannelCard(c, templates, send = ::send) }
        val queue = d.optJSONArray("queue")?.objects() ?: emptyList()
        if (queue.isNotEmpty()) {
            Text("Waiting to be clipped", style = MaterialTheme.typography.titleMedium)
            queue.forEach { q ->
                Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(MaterialTheme.colorScheme.surface).padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(q.optString("name"), fontWeight = FontWeight.SemiBold)
                        Text(q.optString("title"), style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                    IconButton(onClick = { send("/api/watch/unqueue", JSONObject().put("url", q.optString("url"))) }) { Icon(Icons.Default.Close, "Remove from the queue") }
                }
            }
        }
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun ChannelCard(c: JSONObject, templates: List<String>, send: (String, JSONObject, String?) -> Unit) {
    val id = c.optString("id")
    var open by remember(id) { mutableStateOf(false) }
    var askDel by remember(id) { mutableStateOf(false) }
    val upd = { k: String, v: Any -> send("/api/watch/update", JSONObject().put("id", id).put(k, v), "Saved") }
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(when (c.optString("platform")) { "twitch" -> "🟣"; "tiktok" -> "🎵"; else -> "▶️" }, fontSize = 18.sp)
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text(c.optString("name"), style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    val (label, col) = when {
                        c.optString("error").isNotEmpty() -> "⚠ " + c.optString("error").take(50) to Bad
                        c.optString("status") == "live" -> "LIVE" + c.optString("live_now").let { if (it.isNotEmpty() && it != "live") ": " + it.take(40) else "" } to Bad
                        c.optString("status") == "new" -> "Waiting for the first check" to MaterialTheme.colorScheme.onSurfaceVariant
                        else -> "Offline" to MaterialTheme.colorScheme.onSurfaceVariant
                    }
                    Text(label, color = col, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                IconButton(onClick = { open = !open }) { Icon(if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore, "Settings for this channel") }
            }
            if (c.optString("last_upload").isNotEmpty()) Text("Last new video: " + c.optString("last_upload").take(70), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            AnimatedVisibility(open) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) { Text("Clip live streams", Modifier.weight(1f)); Switch(c.optBoolean("live"), { upd("live", it) }) }
                    if (c.optString("platform") == "youtube") {
                        Row(verticalAlignment = Alignment.CenterVertically) { Text("Clip new uploads", Modifier.weight(1f)); Switch(c.optBoolean("uploads"), { upd("uploads", it) }) }
                        Stepper("Only videos longer than", c.optInt("min_len"), 0, 600, 5, " min") { upd("min_len", it) }
                        Stepper("Clips per video", c.optInt("clips", 5), 1, 20, 1, "") { upd("clips", it) }
                    }
                    if (templates.isNotEmpty()) Picker("Template", c.optString("template"), listOf("") + templates, Modifier.fillMaxWidth()) { upd("template", it) }
                    Picker("Content", c.optString("genre", "auto"), listOf("auto", "stream", "music", "trailer", "podcast", "business", "sports", "vlog"), Modifier.fillMaxWidth()) { upd("genre", it) }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (c.optString("platform") == "youtube") OutlinedButton(onClick = { send("/api/watch/latest", JSONObject().put("id", id), "Queued their latest video") }, Modifier.weight(1f)) {
                            Icon(Icons.Default.ContentCut, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("Clip latest video")
                        }
                        OutlinedButton(onClick = { askDel = true }, Modifier.weight(1f)) { Icon(Icons.Default.Delete, null, Modifier.size(16.dp), tint = Bad); Spacer(Modifier.width(6.dp)); Text("Stop watching", color = Bad) }
                    }
                }
            }
        }
    }
    if (askDel) AlertDialog(onDismissRequest = { askDel = false }, title = { Text("Stop watching ${c.optString("name")}?") },
        confirmButton = { TextButton(onClick = { askDel = false; send("/api/watch/remove", JSONObject().put("id", id), "Removed") }) { Text("Stop watching", color = Bad) } },
        dismissButton = { TextButton(onClick = { askDel = false }) { Text("Keep") } })
}

@Composable
private fun Stepper(label: String, value: Int, min: Int, max: Int, step: Int, unit: String, onSet: (Int) -> Unit) {
    var v by remember(value) { mutableStateOf(value) }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f))
        OutlinedButton(onClick = { if (v - step >= min) { v -= step; onSet(v) } }) { Text("−") }
        Text("$v$unit", Modifier.padding(horizontal = 10.dp), fontWeight = FontWeight.SemiBold)
        OutlinedButton(onClick = { if (v + step <= max) { v += step; onSet(v) } }) { Text("+") }
    }
}
