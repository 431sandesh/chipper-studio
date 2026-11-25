package com.chipper.studio.ui

import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.FileProvider
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import com.chipper.studio.AppModel
import com.chipper.studio.net.objects
import com.chipper.studio.net.strings
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.Calendar

@OptIn(UnstableApi::class)
@Composable
fun rememberPlayer(model: AppModel, url: String): ExoPlayer {
    val ctx = LocalContext.current
    val player = remember(url) {
        ExoPlayer.Builder(ctx).setMediaSourceFactory(DefaultMediaSourceFactory(OkHttpDataSource.Factory(model.api.http))).build().apply {
            setMediaItem(MediaItem.fromUri(url)); repeatMode = Player.REPEAT_MODE_ONE; prepare(); playWhenReady = true
        }
    }
    DisposableEffect(player) { onDispose { player.release() } }
    return player
}

@OptIn(UnstableApi::class)
@Composable
fun VideoView(player: ExoPlayer, modifier: Modifier = Modifier, controls: Boolean = true) {
    AndroidView(factory = { c ->
        PlayerView(c).apply {
            this.player = player; useController = controls; resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
            setShutterBackgroundColor(android.graphics.Color.BLACK)
        }
    }, modifier = modifier)
}

private fun download(ctx: Context, model: AppModel, name: String) {
    val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
    val r = DownloadManager.Request(Uri.parse(model.api.url("/media/${enc(name)}.mp4")))
        .addRequestHeader("Cookie", model.api.cookieHeader())
        .setTitle("$name.mp4").setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
        .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Chipper/$name.mp4")
    dm.enqueue(r)
}

@OptIn(UnstableApi::class)
@Composable
fun ClipScreen(model: AppModel, clip: JSONObject, onClose: () -> Unit, onEdit: () -> Unit, onThumb: () -> Unit = {}, onDeleted: () -> Unit = onClose) {
    val ctx = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val name = clip.optString("name")
    val player = rememberPlayer(model, model.api.url("/media/${enc(name)}.mp4?${clip.optDouble("rendered", 0.0)}"))
    var sheet by remember { mutableStateOf(false) }
    var askDelete by remember { mutableStateOf(false) }
    var sharing by remember { mutableStateOf(false) }
    val ready = clip.optString("status") == "done"

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        if (ready) VideoView(player, Modifier.fillMaxSize())
        else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                if (clip.optString("status") != "error") CircularProgressIndicator(color = Pink)
                Spacer(Modifier.height(12.dp))
                Text(if (clip.optString("status") == "error") clip.optString("error").ifEmpty { "This clip failed" } else "Still being made on the laptop…", color = Color.White)
            }
        }
        Row(Modifier.fillMaxWidth().statusBarsPadding().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            RoundIcon(Icons.Default.Close, "Close", onClose)
            Spacer(Modifier.weight(1f))
            if (ready) {
                RoundIcon(Icons.Default.Download, "Download") { download(ctx, model, name); toast.show("Downloading to Downloads/Chipper") }
                Spacer(Modifier.width(8.dp))
                RoundIcon(Icons.Default.Share, "Share") {
                    if (sharing) return@RoundIcon
                    sharing = true; toast.show("Getting the video…")
                    scope.launch {
                        runCatching { model.api.download("/media/${enc(name)}.mp4", File(ctx.cacheDir, "shared/$name.mp4")) }
                            .onSuccess { f ->
                                val uri = FileProvider.getUriForFile(ctx, "com.chipper.studio.files", f)
                                val i = Intent(Intent.ACTION_SEND).setType("video/mp4").putExtra(Intent.EXTRA_STREAM, uri)
                                    .putExtra(Intent.EXTRA_TEXT, (clip.optString("caption") + "\n" + (clip.optJSONArray("hashtags")?.strings()?.joinToString(" ") ?: "")).trim())
                                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                ctx.startActivity(Intent.createChooser(i, "Share clip"))
                            }.onFailure { toast.show(it.message ?: "Could not share") }
                        sharing = false
                    }
                }
                Spacer(Modifier.width(8.dp))
            }
            RoundIcon(Icons.Default.Delete, "Delete") { askDelete = true }
        }
        if (ready) Row(Modifier.align(Alignment.BottomCenter).fillMaxWidth().navigationBarsPadding().padding(16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = { player.pause(); onEdit() }, Modifier.weight(1f).height(54.dp), shape = RoundedCornerShape(16.dp),
                colors = ButtonDefaults.outlinedButtonColors(containerColor = Color.Black.copy(alpha = 0.5f), contentColor = Color.White)) {
                Icon(Icons.Default.Edit, null); Spacer(Modifier.width(8.dp)); Text("Edit", fontWeight = FontWeight.Bold)
            }
            Button(onClick = { sheet = true }, Modifier.weight(1f).height(54.dp), shape = RoundedCornerShape(16.dp)) {
                Icon(Icons.Default.Send, null); Spacer(Modifier.width(8.dp)); Text("Post", fontWeight = FontWeight.Bold)
            }
        }
    }
    if (sheet) PostSheet(model, clip, onDismiss = { sheet = false }, onThumb = { sheet = false; player.pause(); onThumb() })
    if (askDelete) AlertDialog(
        onDismissRequest = { askDelete = false },
        title = { Text("Delete this clip?") }, text = { Text("The video and its files are removed from the laptop. This can't be undone.") },
        confirmButton = { TextButton(onClick = {
            askDelete = false
            scope.launch { runCatching { model.api.delete("/api/clips/${enc(name)}") }.onSuccess { toast.show("Clip deleted"); onDeleted() }.onFailure { toast.show(it.message ?: "Failed") } }
        }) { Text("Delete", color = Bad) } },
        dismissButton = { TextButton(onClick = { askDelete = false }) { Text("Keep") } },
    )
}

@Composable
fun RoundIcon(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, onClick: () -> Unit) {
    Box(Modifier.size(46.dp).clip(RoundedCornerShape(14.dp)).background(Color.Black.copy(alpha = 0.45f)).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Icon(icon, label, tint = Color.White)
    }
}

/** A place a clip can be posted to: id like "yt:UC…" and a label. */
data class Target(val id: String, val label: String, val platform: String)

fun targetsOf(acc: JSONObject): List<Target> {
    val out = mutableListOf<Target>()
    acc.optJSONObject("youtube")?.optJSONArray("channels")?.objects()?.forEach { out += Target("yt:" + it.optString("id"), it.optString("title"), "YouTube") }
    acc.optJSONObject("instagram")?.optJSONArray("accounts")?.objects()?.forEach { out += Target("ig:" + it.optString("id"), "@" + it.optString("username"), "Instagram") }
    acc.optJSONObject("facebook")?.optJSONArray("pages")?.objects()?.forEach { out += Target("fb:" + it.optString("id"), it.optString("name"), "Facebook") }
    acc.optJSONObject("tiktok")?.optJSONArray("accounts")?.objects()?.forEach { out += Target("tt:" + it.optString("id"), "@" + it.optString("username").ifEmpty { it.optString("name") }, "TikTok") }
    return out
}

@kotlin.OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun PostSheet(model: AppModel, clip: JSONObject, onDismiss: () -> Unit, onThumb: () -> Unit) {
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val name = clip.optString("name")
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var title by remember { mutableStateOf(clip.optString("yt_title")) }
    var caption by remember { mutableStateOf(clip.optString("caption")) }
    var tags by remember { mutableStateOf(clip.optJSONArray("hashtags")?.strings()?.joinToString(" ") ?: "") }
    var targets by remember { mutableStateOf<List<Target>>(emptyList()) }
    var groups by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    val picked = remember { mutableStateListOf<String>() }
    var whenAt by remember { mutableStateOf(0L) }      // 0 = now, else unix seconds
    var privacy by remember { mutableStateOf("public") }
    var busy by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        runCatching { model.api.get("/api/accounts") }.onSuccess { j ->
            val a = j.optJSONObject("accounts") ?: JSONObject()
            targets = targetsOf(a); groups = a.optJSONArray("groups")?.objects() ?: emptyList()
        }.onFailure { toast.show(it.message ?: "Could not load accounts") }
    }
    suspend fun save(): Boolean = runCatching {
        model.api.post("/api/clips/${enc(name)}/meta", JSONObject().put("yt_title", title).put("caption", caption).put("hashtags", tags))
    }.onFailure { toast.show(it.message ?: "Could not save") }.isSuccess

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = state, containerColor = MaterialTheme.colorScheme.surface) {
        Column(Modifier.fillMaxWidth().imePadding().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp).padding(bottom = 18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Post this clip", style = MaterialTheme.typography.titleLarge)
            Row(verticalAlignment = Alignment.CenterVertically) {
                AsyncImage(coverRequest(model, clip), "Thumbnail", Modifier.width(72.dp).aspectRatio(9f / 16f).clip(RoundedCornerShape(10.dp)).background(Color.Black).clickable(onClick = onThumb), contentScale = ContentScale.Crop)
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text("Thumbnail", style = MaterialTheme.typography.titleSmall)
                    Text("Used on YouTube, Instagram and Facebook", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(6.dp))
                    OutlinedButton(onClick = onThumb, shape = RoundedCornerShape(10.dp)) { Icon(Icons.Default.Image, null, Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)); Text("Edit thumbnail") }
                }
            }
            OutlinedTextField(title, { title = it }, Modifier.fillMaxWidth(), label = { Text("Title (YouTube)") })
            OutlinedTextField(caption, { caption = it }, Modifier.fillMaxWidth(), label = { Text("Caption") }, minLines = 3)
            OutlinedTextField(tags, { tags = it }, Modifier.fillMaxWidth(), label = { Text("Hashtags") })

            Text("Post to", style = MaterialTheme.typography.titleSmall)
            if (groups.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                groups.forEach { g ->
                    val ids = g.optJSONArray("targets")?.strings() ?: emptyList()
                    val on = ids.isNotEmpty() && picked.containsAll(ids)
                    FilterChip(on, { if (on) picked.removeAll(ids) else ids.forEach { if (it !in picked) picked += it } }, label = { Text(g.optString("name")) },
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = Violet.copy(alpha = 0.18f)))
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                targets.forEach { t ->
                    FilterChip(t.id in picked, { if (t.id in picked) picked.remove(t.id) else picked += t.id }, label = { Text("${t.platform} ${t.label}") },
                        colors = FilterChipDefaults.filterChipColors(selectedContainerColor = Pink.copy(alpha = 0.16f)))
                }
            }
            if (targets.isEmpty()) Text("No accounts connected yet. Connect them on the laptop.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

            Text("When", style = MaterialTheme.typography.titleSmall)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                fun at(h: Int, dayPlus: Int = 0): Long = Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, dayPlus); set(Calendar.HOUR_OF_DAY, h); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0) }.timeInMillis / 1000
                val now = System.currentTimeMillis() / 1000
                val opts = listOf("Now" to 0L, "In 1 hour" to now + 3600, "In 3 hours" to now + 3 * 3600,
                    "Tonight 8 pm" to at(20).let { if (it < now + 600) at(20, 1) else it }, "Tomorrow 9 am" to at(9, 1))
                opts.forEach { (l, v) -> FilterChip(whenAt == v || (v != 0L && whenAt != 0L && kotlin.math.abs(whenAt - v) < 60), { whenAt = v }, label = { Text(l) }) }
            }
            if (picked.any { it.startsWith("yt:") }) {
                Text("YouTube privacy", style = MaterialTheme.typography.titleSmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("public", "unlisted", "private").forEach { v -> FilterChip(privacy == v, { privacy = v }, label = { Text(v.replaceFirstChar { it.uppercase() }) }) }
                }
            }
            PostStatus(clip)
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedButton(onClick = { busy = true; scope.launch { if (save()) toast.show("Saved"); busy = false } }, Modifier.weight(1f).height(52.dp), enabled = !busy, shape = RoundedCornerShape(14.dp)) { Text("Save") }
                Button(onClick = {
                    busy = true
                    scope.launch {
                        if (save()) runCatching {
                            model.api.post("/api/clips/${enc(name)}/post", JSONObject().put("platforms", JSONArray(picked.toList())).put("privacy", privacy).apply { if (whenAt > 0) put("at", whenAt) })
                        }.onSuccess { toast.show(if (whenAt > 0) "Scheduled" else "Posting… you'll see the status here"); onDismiss() }
                            .onFailure { toast.show(it.message ?: "Could not post") }
                        busy = false
                    }
                }, Modifier.weight(1f).height(52.dp), enabled = !busy && picked.isNotEmpty(), shape = RoundedCornerShape(14.dp)) {
                    Text(if (whenAt > 0) "Schedule" else "Publish", fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}

@Composable
private fun PostStatus(clip: JSONObject) {
    val posts = clip.optJSONObject("posts") ?: return
    if (posts.length() == 0) return
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("Posts", style = MaterialTheme.typography.titleSmall)
        posts.keys().forEach { k ->
            val p = posts.optJSONObject(k) ?: return@forEach
            val st = p.optString("status")
            Text("${k.substringBefore(':').uppercase()}  ·  $st${if (p.optString("error").isNotEmpty()) " – " + p.optString("error") else ""}",
                style = MaterialTheme.typography.bodySmall, color = when (st) { "posted" -> Good; "error" -> Bad; else -> MaterialTheme.colorScheme.onSurfaceVariant })
        }
    }
}
