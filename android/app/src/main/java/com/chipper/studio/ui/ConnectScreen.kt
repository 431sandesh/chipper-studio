package com.chipper.studio.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.chipper.studio.AppModel
import com.chipper.studio.Link
import com.chipper.studio.R
import kotlinx.coroutines.launch

@Composable
fun StatusDot(link: Link, size: Int = 10) {
    val pulse = rememberInfiniteTransition(label = "pulse")
    val a by pulse.animateFloat(0.35f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "a")
    val c = when (link) { Link.ONLINE -> Good; Link.OFFLINE -> Color(0xFF9AA0B0); Link.CHECKING -> Warn }
    Box(Modifier.size(size.dp).clip(CircleShape).alpha(if (link == Link.CHECKING) a else 1f).background(c))
}

@Composable
fun ConnectScreen(model: AppModel, onEnter: () -> Unit) {
    val scope = rememberCoroutineScope()
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val p = model.api.prefs
    var pw by remember { mutableStateOf("") }
    var err by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var setup by remember { mutableStateOf(p.ntfyTopic.isEmpty() && p.manualBase.isEmpty()) }
    var link by remember { mutableStateOf(p.manualBase) }
    var topic by remember { mutableStateOf(p.ntfyTopic) }

    Column(
        Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).systemBarsPadding().imePadding()
            .verticalScroll(rememberScrollState()).padding(20.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.height(28.dp))
        Image(painterResource(R.drawable.logo), "Chipper Studio", Modifier.width(190.dp))
        Spacer(Modifier.height(28.dp))

        Card(
            Modifier.fillMaxWidth(), shape = RoundedCornerShape(22.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(2.dp),
        ) {
            Column(Modifier.padding(18.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    StatusDot(model.link, 12)
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text(
                            when (model.link) { Link.ONLINE -> "Laptop online"; Link.OFFLINE -> "Laptop offline"; Link.CHECKING -> "Looking for your laptop…" },
                            style = MaterialTheme.typography.titleMedium,
                        )
                        if (model.link == Link.ONLINE) Text(p.base.removePrefix("https://"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = { scope.launch { model.check() } }) { Icon(Icons.Default.Refresh, "Check again") }
                }
                if (model.note.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    Text(model.note, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                AnimatedVisibility(model.link == Link.ONLINE) {
                    Column {
                        Spacer(Modifier.height(16.dp))
                        if (model.signedIn) {
                            Button(onClick = onEnter, Modifier.fillMaxWidth().height(52.dp), shape = RoundedCornerShape(14.dp)) {
                                Text("Open Chipper Studio", fontWeight = FontWeight.Bold)
                            }
                        } else {
                            OutlinedTextField(
                                pw, { pw = it; err = "" }, Modifier.fillMaxWidth(), label = { Text("Remote password") },
                                singleLine = true, visualTransformation = PasswordVisualTransformation(),
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Go),
                            )
                            if (err.isNotEmpty()) Text(err, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
                            Spacer(Modifier.height(12.dp))
                            Button(
                                onClick = {
                                    busy = true
                                    scope.launch {
                                        val e = model.api.login(pw)
                                        busy = false
                                        if (e == null) {
                                            model.signedIn = true
                                            // remember the ntfy topic so the app finds the laptop by itself next time
                                            runCatching { model.api.get("/api/app/info") }.getOrNull()?.let { j ->
                                                if (j.optString("ntfy_topic").isNotEmpty()) { p.ntfyTopic = j.optString("ntfy_topic"); topic = p.ntfyTopic }
                                                if (j.optString("ntfy_server").isNotEmpty()) p.ntfyServer = j.optString("ntfy_server")
                                            }
                                            com.chipper.studio.net.ListenService.start(ctx)
                                            onEnter()
                                        } else err = e
                                    }
                                },
                                enabled = pw.length >= 6 && !busy, modifier = Modifier.fillMaxWidth().height(52.dp), shape = RoundedCornerShape(14.dp),
                            ) {
                                if (busy) CircularProgressIndicator(Modifier.size(20.dp), color = Color.White, strokeWidth = 2.dp) else Text("Connect", fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(14.dp))
        TextButton(onClick = { setup = !setup }) {
            Icon(Icons.Default.Settings, null, Modifier.size(18.dp)); Spacer(Modifier.width(6.dp))
            Text(if (setup) "Hide connection settings" else "Connection settings")
        }
        AnimatedVisibility(setup) {
            Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Laptop link", style = MaterialTheme.typography.titleSmall)
                    Text(
                        "Paste the link from the \"Chipper is online\" notification (or the file \"Chipper remote link.txt\" on the laptop). After you connect once, the app finds new links by itself.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedTextField(link, { link = it }, Modifier.fillMaxWidth(), singleLine = true, label = { Text("https://….trycloudflare.com") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                    OutlinedTextField(topic, { topic = it }, Modifier.fillMaxWidth(), singleLine = true, label = { Text("ntfy topic (filled in after connecting)") })
                    OutlinedButton(onClick = {
                        p.manualBase = link; p.ntfyTopic = topic
                        if (link.isNotEmpty()) p.base = link.trim().trimEnd('/')
                        scope.launch { model.check() }
                    }, Modifier.fillMaxWidth()) { Text("Save and check") }
                }
            }
        }
        Spacer(Modifier.height(30.dp))
    }
}
