package com.chipper.studio.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCut
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.chipper.studio.AppModel
import com.chipper.studio.net.objects
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** Templates saved on the laptop, for the Start forms. */
@Composable
fun rememberTemplates(model: AppModel): List<String> {
    val list = remember { mutableStateListOf<String>() }
    LaunchedEffect(Unit) {
        (model.cached("/api/config") ?: runCatching { model.load("/api/config") }.getOrNull())?.optJSONObject("templates")?.let { t ->
            list.clear(); list.addAll(t.keys().asSequence().toList().sorted())
        }
    }
    return list
}

@Composable
fun Picker(label: String, value: String, options: List<String>, modifier: Modifier = Modifier, onPick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box(modifier) {
        OutlinedButton(onClick = { open = true }, Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) {
            Text("$label: ${value.ifEmpty { "Default" }}", maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        DropdownMenu(open, { open = false }) {
            options.forEach { o -> DropdownMenuItem(text = { Text(o) }, onClick = { onPick(o); open = false }) }
        }
    }
}

@Composable
fun LiveScreen(model: AppModel) {
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var jobs by remember { mutableStateOf(model.cached("/api/state")?.optJSONArray("jobs")?.objects() ?: emptyList()) }
    val templates = rememberTemplates(model)
    LaunchedEffect(Unit) {
        while (true) {
            runCatching { model.load("/api/state") }.onSuccess { jobs = it.getJSONArray("jobs").objects() }
            delay(2500)
        }
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("Live streams", style = MaterialTheme.typography.titleLarge)
        Text("Paste a Twitch, YouTube or Kick live link. Chipper records it on the laptop and cuts clips when chat goes wild.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (jobs.isEmpty()) repeat(3) { CardSkeleton() }
        jobs.forEach { j -> SlotCard(j, templates) { path, body ->
            scope.launch {
                runCatching { model.api.post(path, body) }
                    .onSuccess { toast.show(when (path) { "/api/start" -> "Starting…"; "/api/stop" -> "Stopped"; else -> "Clipping this moment" }) }
                    .onFailure { toast.show(it.message ?: "Failed") }
            }
        } }
    }
}

@Composable
private fun SlotCard(j: JSONObject, templates: List<String>, act: (String, JSONObject) -> Unit) {
    val slot = j.optInt("slot", 1)
    val state = j.optString("state", "idle")
    val running = state !in listOf("idle", "error", "stopped", "done")
    var url by remember(slot) { mutableStateOf(j.optString("url")) }
    var tpl by remember(slot) { mutableStateOf(j.optString("template")) }
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(10.dp).clip(CircleShape).background(if (state == "live") Bad else if (running) Warn else MaterialTheme.colorScheme.outline))
                Spacer(Modifier.width(8.dp))
                Text(j.optString("name").ifEmpty { "Stream $slot" }, style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(state.replaceFirstChar { it.uppercase() }, style = MaterialTheme.typography.labelLarge, color = if (state == "live") Bad else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (running) {
                val chart = j.optJSONArray("chart") ?: JSONArray()
                if (chart.length() > 1) ChatChart(chart)
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    Text("${j.optInt("clips")} clips", style = MaterialTheme.typography.bodyMedium)
                    Text(if (j.optBoolean("chat_connected")) "Chat connected" else "Chat not connected", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (j.optString("campaign").isNotEmpty()) Text(j.optString("campaign"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button(onClick = { act("/api/clip_now", JSONObject().put("slot", slot)) }, Modifier.weight(1f), enabled = state == "live", shape = RoundedCornerShape(12.dp)) {
                        Icon(Icons.Default.ContentCut, null, Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)); Text("Clip now")
                    }
                    OutlinedButton(onClick = { act("/api/stop", JSONObject().put("slot", slot)) }, Modifier.weight(1f), shape = RoundedCornerShape(12.dp)) {
                        Icon(Icons.Default.Stop, null, Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)); Text("Stop")
                    }
                }
            } else {
                OutlinedTextField(url, { url = it }, Modifier.fillMaxWidth(), singleLine = true, label = { Text("Live link") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                if (templates.isNotEmpty()) Picker("Template", tpl, templates, Modifier.fillMaxWidth()) { tpl = it }
                Button(
                    onClick = { act("/api/start", JSONObject().put("slot", slot).put("url", url.trim()).put("template", tpl)) },
                    enabled = url.startsWith("http"), modifier = Modifier.fillMaxWidth().height(48.dp), shape = RoundedCornerShape(12.dp),
                ) { Icon(Icons.Default.PlayArrow, null); Spacer(Modifier.width(6.dp)); Text("Start recording", fontWeight = FontWeight.Bold) }
            }
        }
    }
}

/** Chat messages per interval, as a small line chart. */
@Composable
private fun ChatChart(chart: JSONArray) {
    val vals = (0 until chart.length()).map { i -> chart.optJSONArray(i)?.optDouble(1, 0.0) ?: chart.optDouble(i, 0.0) }
    val line = MaterialTheme.colorScheme.primary
    Canvas(Modifier.fillMaxWidth().height(54.dp)) {
        val mx = (vals.maxOrNull() ?: 1.0).coerceAtLeast(1.0)
        val p = Path()
        vals.forEachIndexed { i, v ->
            val o = Offset(size.width * i / (vals.size - 1).coerceAtLeast(1), size.height * (1 - (v / mx).toFloat()))
            if (i == 0) p.moveTo(o.x, o.y) else p.lineTo(o.x, o.y)
        }
        drawPath(p, line, style = Stroke(width = 4f))
    }
}

@Composable
fun VideosScreen(model: AppModel) {
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var vod by remember { mutableStateOf(model.cached("/api/state")?.optJSONObject("vod")) }
    var url by remember { mutableStateOf("") }
    var clips by remember { mutableStateOf(5) }
    var tpl by remember { mutableStateOf("") }
    val templates = rememberTemplates(model)
    LaunchedEffect(Unit) {
        while (true) {
            runCatching { model.load("/api/state") }.onSuccess { vod = it.optJSONObject("vod") ?: JSONObject() }
            delay(2000)
        }
    }
    val v = vod
    val state = v?.optString("state", "idle") ?: "idle"
    val busy = state in listOf("downloading", "analyzing", "picking", "rendering")
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("Clip a video", style = MaterialTheme.typography.titleLarge)
        Text("A finished stream, YouTube video, music video or trailer. Chipper finds the best moments and makes clips.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        if (v == null) CardSkeleton(4) else Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (busy) {
                    Text(v.optString("title").ifEmpty { v.optString("source") }, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    LinearProgressIndicator(progress = { (v.optDouble("pct", 0.0) / 100).toFloat() }, modifier = Modifier.fillMaxWidth())
                    Text(v.optString("msg"), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    OutlinedButton(onClick = { scope.launch { runCatching { model.api.post("/api/vod/stop") }; toast.show("Stopping") } }, Modifier.fillMaxWidth()) { Text("Stop") }
                } else {
                    if (state == "done") Text("Done: ${v.optJSONArray("made")?.length() ?: 0} clips. Find them in Clips.", color = Good)
                    if (state == "error") Text(v.optString("msg"), color = Bad)
                    OutlinedTextField(url, { url = it }, Modifier.fillMaxWidth(), singleLine = true, label = { Text("Video link") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Clips to make", Modifier.weight(1f))
                        OutlinedButton(onClick = { if (clips > 1) clips-- }) { Text("−") }
                        Text("$clips", Modifier.padding(horizontal = 14.dp), style = MaterialTheme.typography.titleMedium)
                        OutlinedButton(onClick = { if (clips < 20) clips++ }) { Text("+") }
                    }
                    if (templates.isNotEmpty()) Picker("Template", tpl, templates, Modifier.fillMaxWidth()) { tpl = it }
                    Button(onClick = {
                        scope.launch {
                            runCatching { model.api.post("/api/vod/start", JSONObject().put("url", url.trim()).put("clips", clips).put("template", tpl)) }
                                .onSuccess { toast.show("Started") }.onFailure { toast.show(it.message ?: "Failed") }
                        }
                    }, enabled = url.startsWith("http"), modifier = Modifier.fillMaxWidth().height(48.dp), shape = RoundedCornerShape(12.dp)) { Text("Make clips", fontWeight = FontWeight.Bold) }
                }
            }
        }
    }
}
