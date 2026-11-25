package com.chipper.studio.editor

import android.graphics.Bitmap
import androidx.activity.compose.BackHandler
import androidx.annotation.OptIn
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Redo
import androidx.compose.material.icons.automirrored.filled.Undo
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.ChatBubbleOutline
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ContentCut
import androidx.compose.material.icons.filled.Crop
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.FastForward
import androidx.compose.material.icons.filled.FastRewind
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Label
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material.icons.filled.TextFields
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.VideoSize
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import coil.ImageLoader
import coil.request.ImageRequest
import coil.request.SuccessResult
import com.chipper.studio.AppModel
import com.chipper.studio.net.objects
import com.chipper.studio.net.strings
import com.chipper.studio.ui.Bad
import com.chipper.studio.ui.LocalToast
import com.chipper.studio.ui.Mint
import com.chipper.studio.ui.Pink
import com.chipper.studio.ui.PinkDeep
import com.chipper.studio.ui.Violet
import com.chipper.studio.ui.enc
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max
import kotlin.math.min

enum class Tool(val label: String, val icon: ImageVector) {
    LAYOUT("Layout", Icons.Default.Dashboard), EDIT("Edit", Icons.Default.ContentCut), CAPTIONS("Captions", Icons.Default.ChatBubbleOutline),
    TEXT("Text", Icons.Default.TextFields), IMAGE("Image", Icons.Default.Image), AUDIO("Audio & FX", Icons.Default.MusicNote), THIRDS("Thirds", Icons.Default.Label)
}

fun fmt(t: Double): String { val s = max(0.0, t); return "%d:%04.1f".format((s / 60).toInt(), s % 60) }

/** The native editor. Loads the clip, plays the source video through GL with every layer drawn on top, and saves back to the laptop. */
@OptIn(UnstableApi::class)
@Composable
fun EditorScreen(app: AppModel, name: String, onClose: () -> Unit, onWeb: () -> Unit = {}) {
    val toast = LocalToast.current
    var model by remember { mutableStateOf<EdModel?>(null) }
    var err by remember { mutableStateOf("") }
    LaunchedEffect(name) {
        runCatching { app.api.get("/api/clips/${enc(name)}/full?scene=1") }
            .onSuccess { j ->
                val c = j.getJSONObject("clip")
                if (!c.optBoolean("has_source", true)) err = "The source of this clip was deleted, so it can't be edited."
                else model = EdModel(c, j.optJSONObject("styles") ?: JSONObject())
            }.onFailure { err = it.message ?: "Could not open the clip" }
    }
    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        val m = model
        if (m == null) {
            Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
                if (err.isEmpty()) CircularProgressIndicator(color = Pink) else Text(err, Modifier.padding(24.dp), color = Bad)
                TextButton(onClick = onClose) { Text("Close") }
            }
        } else Editor(app, m, onClose, onWeb)
    }
}

@OptIn(UnstableApi::class)
@Composable
private fun Editor(app: AppModel, m: EdModel, onClose: () -> Unit, onWeb: () -> Unit) {
    val ctx = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var t by remember { mutableDoubleStateOf(m.start + 0.2) }
    var playing by remember { mutableStateOf(false) }
    var tool by remember { mutableStateOf<Tool?>(null) }
    var cropMode by remember { mutableStateOf(false) }
    var askClose by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    val images = remember { mutableStateMapOf<String, Bitmap>() }

    val player = remember {
        ExoPlayer.Builder(ctx).setMediaSourceFactory(DefaultMediaSourceFactory(OkHttpDataSource.Factory(app.api.http))).build().apply {
            setMediaItem(MediaItem.fromUri(app.api.url("/rawlite/${enc(m.name)}.mp4"))); prepare()
        }
    }
    val gl = remember { VideoGL(ctx) { s -> player.setVideoSurface(s) } }
    DisposableEffect(Unit) {
        val l = object : Player.Listener {
            override fun onVideoSizeChanged(v: VideoSize) { if (v.width > 0) { m.srcW = v.width; m.srcH = v.height; m.list().filter { it.optString("kind") == "video" }.forEach(m::fitCrop); m.rev++ } }
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY && m.dur <= 0) {
                    m.dur = player.duration / 1000.0
                    if (m.end <= m.start || m.end > m.dur) m.end = m.dur
                    player.seekTo((t * 1000).toLong())
                }
            }
            override fun onIsPlayingChanged(p: Boolean) { playing = p }
        }
        player.addListener(l)
        onDispose { player.removeListener(l); player.release(); gl.release() }
    }
    // images used by the clip (loaded with the login cookie)
    LaunchedEffect(m.rev) {
        m.list().filter { it.optString("kind") == "image" }.map { it.optString("file") }.filter { it.isNotEmpty() && it !in images }.forEach { f ->
            val r = ImageLoader(ctx).execute(ImageRequest.Builder(ctx).data(app.api.url("/assets/${enc(f)}")).addHeader("Cookie", app.api.cookieHeader()).allowHardware(false).build())
            ((r as? SuccessResult)?.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap?.let { images[f] = it; m.imgAr[f] = it.width.toFloat() / it.height }
        }
    }
    // the clock: follow the player, keep inside the clip, jump over cuts, and feed the GL view
    LaunchedEffect(Unit) {
        while (true) {
            withFrameNanos { }
            if (player.isPlaying) {
                var now = player.currentPosition / 1000.0
                m.inCut(now)?.let { now = it.second; player.seekTo((now * 1000).toLong()) }
                if (now >= m.end - 0.03) { now = m.start; player.seekTo((now * 1000).toLong()) }
                t = now
            }
            gl.frame = buildFrame(m, t)
        }
    }
    fun seek(v: Double) { t = v.coerceIn(0.0, max(m.dur, m.end)); player.seekTo((t * 1000).toLong()) }
    BackHandler { if (m.dirty) askClose = true else onClose() }

    Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()) {
        // top bar
        Row(Modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { if (m.dirty) askClose = true else onClose() }) { Icon(Icons.Default.Close, "Close") }
            IconButton(onClick = { m.undo() }, enabled = m.rev >= 0 && m.canUndo) { Icon(Icons.AutoMirrored.Filled.Undo, "Undo") }
            IconButton(onClick = { m.redo() }, enabled = m.rev >= 0 && m.canRedo) { Icon(Icons.AutoMirrored.Filled.Redo, "Redo") }
            Spacer(Modifier.weight(1f))
            m.selected()?.let {
                IconButton(onClick = { deleteSel(m) }) { Icon(Icons.Default.Delete, "Delete the selected layer", tint = Bad) }
            }
            Button(onClick = {
                saving = true
                scope.launch {
                    runCatching { app.api.post("/api/clips/${enc(m.name)}/edit", m.payload()) }
                        .onSuccess { m.dirty = false; toast.show("Saved. Rendering on the laptop…"); onClose() }
                        .onFailure { toast.show(it.message ?: "Could not save") }
                    saving = false
                }
            }, enabled = !saving, shape = RoundedCornerShape(12.dp)) {
                if (saving) CircularProgressIndicator(Modifier.size(18.dp), color = Color.White, strokeWidth = 2.dp)
                else { Icon(Icons.Default.Save, null, Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)); Text("Render", fontWeight = FontWeight.Bold) }
            }
        }

        // preview
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth().padding(vertical = 4.dp), contentAlignment = Alignment.Center) {
            val hW = min(maxWidth.value, maxHeight.value * 9 / 16)
            Box(Modifier.width(hW.dp).aspectRatio(9f / 16f).clip(RoundedCornerShape(14.dp)).background(Color.Black)) {
                AndroidView({ gl }, Modifier.fillMaxSize())
                val density = LocalDensity.current
                val k = with(density) { hW.dp.toPx() } / W
                Canvas(Modifier.fillMaxSize().pointerInput(m, cropMode) { gestures(m, k, cropMode, { t }) }) {
                    m.rev // redraw on changes
                    drawIntoCanvas { c ->
                        val nc = c.nativeCanvas
                        val fx = FxLook.at(m, t)
                        nc.save()
                        if (fx.scale != 1f || fx.dx != 0f || fx.dy != 0f) { nc.translate(fx.dx * k * fx.scale, fx.dy * k * fx.scale); nc.scale(fx.scale, fx.scale, size.width / 2, size.height / 2) }
                        Overlay.draw(nc, m, t, k, images, true)
                        nc.restore()
                        FxLook.drawTop(nc, fx, size.width, size.height)
                    }
                }
                if (cropMode) Text("Crop: drag to move, pinch to zoom", Modifier.align(Alignment.TopCenter).padding(8.dp).clip(RoundedCornerShape(8.dp)).background(Color.Black.copy(alpha = 0.6f)).padding(horizontal = 10.dp, vertical = 4.dp), color = Mint, fontSize = 12.sp)
            }
        }

        // transport
        Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { seek(t - 1) }) { Icon(Icons.Default.FastRewind, "Back 1 second") }
            Box(Modifier.size(44.dp).clip(CircleShape).background(Pink).clickable { if (player.isPlaying) player.pause() else { if (t >= m.end - 0.05) seek(m.start); player.play() } }, contentAlignment = Alignment.Center) {
                Icon(if (playing) Icons.Default.Pause else Icons.Default.PlayArrow, "Play or pause", tint = Color.White)
            }
            IconButton(onClick = { seek(t + 1) }) { Icon(Icons.Default.FastForward, "Forward 1 second") }
            Text("${fmt(t - m.start)} / ${fmt(m.len())}", style = MaterialTheme.typography.labelLarge)
        }
        Timeline(m, t, onSeek = { seek(it) })

        // contextual tray, then the toolbar
        val sel = m.selected()
        val showObj = sel != null && sel.optString("kind") in setOf("video", "image")
        Box(Modifier.fillMaxWidth()) {
            when {
                showObj -> ObjectTray(m, sel!!, t, cropMode, { cropMode = it }, onDone = { m.sel = null; cropMode = false })
                tool == Tool.EDIT -> EditTray(m, t)
                tool == Tool.CAPTIONS -> CaptionTray(m)
                tool == Tool.TEXT -> TextTray(m, t)
                tool == Tool.LAYOUT -> LayoutTray(m)
                tool == Tool.IMAGE -> ImageTray(app, m, t)
                tool == Tool.AUDIO -> FxTray(app, m, t) { sec ->
                    player.play()
                    android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({ player.pause() }, (sec * 1000).toLong())
                }
                tool == Tool.THIRDS -> ThirdsTray(app, m, t)
                else -> {}
            }
        }
        Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface).horizontalScroll(rememberScrollState()).padding(horizontal = 4.dp, vertical = 4.dp)) {
            Tool.entries.forEach { tt ->
                val on = tool == tt && !showObj
                Column(Modifier.width(64.dp).clip(RoundedCornerShape(12.dp)).background(if (on) Pink.copy(alpha = 0.14f) else Color.Transparent)
                    .clickable { m.sel = null; cropMode = false; tool = if (tool == tt) null else tt }.padding(vertical = 6.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(tt.icon, null, tint = if (on) PinkDeep else MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(tt.label, fontSize = 10.5.sp, color = if (on) PinkDeep else MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
    if (askClose) AlertDialog(
        onDismissRequest = { askClose = false }, title = { Text("Leave without rendering?") },
        text = { Text("Your changes to this clip are not saved yet.") },
        confirmButton = { TextButton(onClick = { askClose = false; onClose() }) { Text("Leave", color = Bad) } },
        dismissButton = { TextButton(onClick = { askClose = false }) { Text("Keep editing") } },
    )
}

private fun deleteSel(m: EdModel) {
    val l = m.selected() ?: return
    if (l.optString("kind") in setOf("captions", "hook")) l.put("hidden", true)
    else { val a = m.layers(); for (i in 0 until a.length()) if (a.getJSONObject(i) === l) { a.remove(i); break } }
    m.sel = null; m.commit()
}

/** The background plus every video window, as the GL view draws them. */
private fun buildFrame(m: EdModel, t: Double): Frame {
    val q = mutableListOf<Quad>()
    val bg = m.scene.optJSONObject("bg") ?: JSONObject()
    var bgc = Overlay.color(bg.optString("color"), 0xFF111318.toInt())
    if (bg.optString("type", "blur") == "blur") {
        // cover the 9:16 canvas with the whole stream, blurred and darkened
        val ar = (m.srcW.toFloat() / m.srcH) / (W / H)
        val cw = if (ar > 1) 1f / ar else 1f; val ch = if (ar > 1) 1f else ar
        q += Quad(0f, 0f, W, H, (1 - cw) / 2, (1 - ch) / 2, cw, ch, 0, 0f, 0f, 0, 1f, blur = 3f + bg.optDouble("blur", 14.0).toFloat() / 3, dim = bg.optDouble("dim", 0.25).toFloat())
    } else if (bg.optString("type") == "gradient") bgc = Overlay.color(bg.optString("color2"), bgc)
    for (l in m.order()) {
        if (l.optString("kind") != "video" || l.optBoolean("hidden")) continue
        val c = m.cropAt(l, t)
        val shape = when (l.optString("shape", "rect")) { "rect" -> 0; "circle" -> 2; else -> 1 }
        val w = l.optDouble("w", 1080.0).toFloat(); val h = l.optDouble("h", 1920.0).toFloat()
        val rad = if (l.optString("shape") == "pill") min(w, h) / 2 else min(w, h) * l.optDouble("radius", 0.12).toFloat()
        q += Quad(l.optDouble("x").toFloat(), l.optDouble("y").toFloat(), w, h, c[0].toFloat(), c[1].toFloat(), c[2].toFloat(), c[3].toFloat(),
            shape, rad, l.optDouble("border", 0.0).toFloat(), Overlay.color(l.optString("bcolor"), -1), l.optDouble("opacity", 1.0).toFloat())
    }
    return Frame(bgc, q, FxLook.at(m, t), (t % 60).toFloat())
}

/** Tap to select; drag to move; pinch to resize. In crop mode a video window's picture moves and zooms instead. */
private suspend fun androidx.compose.ui.input.pointer.PointerInputScope.gestures(m: EdModel, k: Float, cropMode: Boolean, now: () -> Double) {
    awaitEachGesture {
        val down = awaitFirstDown(requireUnconsumed = false)
        val px = down.position.x / k; val py = down.position.y / k
        val tRel = now() - m.start
        val cur = m.selected()
        val inside = { l: JSONObject -> val b = m.bounds(l, m.imgAr[l.optString("file")] ?: 1f); px >= b[0] && px <= b[0] + b[2] && py >= b[1] && py <= b[1] + b[3] }
        if (!(cropMode && cur != null)) {
            val hit = if (cur != null && m.visible(cur, tRel) && inside(cur)) cur else m.order().reversed().firstOrNull { m.visible(it, tRel) && inside(it) }
            m.sel = hit?.optString("id")
        }
        val l = m.selected() ?: return@awaitEachGesture
        var moved = false
        do {
            val ev = awaitPointerEvent()
            val pan = ev.calculatePan(); val zoom = ev.calculateZoom()
            if (pan != Offset.Zero || zoom != 1f) {
                moved = true
                val dx = pan.x / k; val dy = pan.y / k
                when (l.optString("kind")) {
                    "video" -> if (cropMode) {
                        val c = m.cropAt(l, now()).toMutableList()
                        val sx = c[2] / l.optDouble("w", 1080.0); val sy = c[3] / l.optDouble("h", 1920.0)
                        var nw = c[2] / zoom; var nh = c[3] / zoom
                        if (nw > 1) { nh /= nw; nw = 1.0 }; if (nh > 1) { nw /= nh; nh = 1.0 }
                        val cx = c[0] + c[2] / 2 - dx * sx; val cy = c[1] + c[3] / 2 - dy * sy
                        l.put("crop", JSONArray(listOf((cx - nw / 2).coerceIn(0.0, 1 - nw), (cy - nh / 2).coerceIn(0.0, 1 - nh), nw, nh)))
                        if ((l.optJSONArray("keys")?.length() ?: 0) > 0) m.setKey(l, now())
                    } else {
                        val w = l.optDouble("w"); val h = l.optDouble("h"); val nw = (w * zoom).coerceIn(80.0, 2400.0); val nh = nw * h / w
                        l.put("x", l.optDouble("x") + dx - (nw - w) / 2).put("y", l.optDouble("y") + dy - (nh - h) / 2).put("w", nw).put("h", nh)
                    }
                    "image" -> l.put("x", l.optDouble("x") + dx).put("y", l.optDouble("y") + dy).put("w", (l.optDouble("w", 300.0) * zoom).coerceIn(30.0, 2400.0))
                    "text" -> l.put("x", l.optDouble("x") + dx).put("y", l.optDouble("y") + dy).put("size", (l.optDouble("size", 70.0) * zoom).coerceIn(14.0, 400.0))
                    "captions", "hook" -> l.put("y", (l.optDouble("y") + dy).coerceIn(60.0, 1860.0)).put("scale", (l.optDouble("scale", 1.0) * zoom).coerceIn(0.4, 2.5))
                    "third" -> l.put("x", l.optDouble("x") + dx).put("y", l.optDouble("y") + dy).put("scale", (l.optDouble("scale", 1.0) * zoom).coerceIn(0.3, 4.0))
                }
                m.rev++
                ev.changes.forEach { it.consume() }
            }
        } while (ev.changes.any { it.pressed })
        if (moved) m.commit()
    }
}

// ------------------------------------------------------------------------------------------------ timeline
@Composable
private fun Timeline(m: EdModel, t: Double, onSeek: (Double) -> Unit) {
    val line = MaterialTheme.colorScheme.outline
    val text = MaterialTheme.colorScheme.onSurfaceVariant
    val surf = MaterialTheme.colorScheme.surfaceVariant
    Canvas(Modifier.fillMaxWidth().height(96.dp).padding(horizontal = 12.dp, vertical = 4.dp).pointerInput(m) {
        awaitEachGesture {
            val d = awaitFirstDown()
            val dur = max(1.0, m.dur)
            val x2t = { x: Float -> (x / size.width * dur).coerceIn(0.0, dur) }
            val hx = { v: Double -> (v / dur * size.width).toFloat() }
            val grab = when {
                kotlin.math.abs(d.position.x - hx(m.start)) < 28 && d.position.y > 20 && d.position.y < 70 -> 1
                kotlin.math.abs(d.position.x - hx(m.end)) < 28 && d.position.y > 20 && d.position.y < 70 -> 2
                else -> 0
            }
            if (grab == 0) onSeek(x2t(d.position.x))
            do {
                val ev = awaitPointerEvent(); val p = ev.changes.first()
                when (grab) {
                    1 -> { m.start = x2t(p.position.x).coerceAtMost(m.end - 3); onSeek(m.start) }
                    2 -> { m.end = x2t(p.position.x).coerceAtLeast(m.start + 3); onSeek(m.end) }
                    else -> onSeek(x2t(p.position.x))
                }
                p.consume()
            } while (ev.changes.any { it.pressed })
            if (grab != 0) m.commit()
        }
    }) {
        m.rev
        val dur = max(1.0, m.dur)
        val x = { v: Double -> (v / dur * size.width).toFloat() }
        // ruler
        val step = listOf(1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0).first { dur / it <= 8 }
        var s = 0.0
        while (s <= dur) {
            drawLine(line, Offset(x(s), 0f), Offset(x(s), 10f), 2f)
            drawIntoCanvas { c -> c.nativeCanvas.drawText(fmt(s).substringBefore('.'), x(s) + 4, 18f, android.graphics.Paint().apply { color = android.graphics.Color.GRAY; textSize = 22f; isAntiAlias = true }) }
            s += step
        }
        // the whole video, the kept part, and cuts
        drawRoundRect(surf, Offset(0f, 26f), Size(size.width, 40f), CornerRadius(10f))
        drawRoundRect(Violet.copy(alpha = 0.55f), Offset(x(m.start), 26f), Size(x(m.end) - x(m.start), 40f), CornerRadius(10f))
        m.cuts.forEach { (a, b) -> drawRect(Color(0xCC555555), Offset(x(a), 26f), Size(x(b) - x(a), 40f)) }
        m.cutA?.let { drawLine(Bad, Offset(x(it), 22f), Offset(x(it), 70f), 4f) }
        listOf(m.start, m.end).forEach { v -> drawRoundRect(Color.White, Offset(x(v) - 7, 22f), Size(14f, 48f), CornerRadius(5f)); drawRoundRect(Violet, Offset(x(v) - 7, 22f), Size(14f, 48f), CornerRadius(5f), style = androidx.compose.ui.graphics.drawscope.Stroke(3f)) }
        // text and lower-third blocks
        m.list().filter { it.optString("kind") in setOf("text", "third") }.forEach { l ->
            val a = m.start + l.optDouble("start", 0.0)
            val e = if (l.optString("kind") == "text") l.optDouble("end", 0.0).let { if (it <= 0) m.len() else it } else l.optDouble("dur", 0.0).let { if (it <= 0) m.len() else l.optDouble("start", 0.0) + it }
            drawRoundRect(if (l.optString("kind") == "text") Pink else Violet, Offset(x(a), 74f), Size(max(6f, x(m.start + e) - x(a)), 16f), CornerRadius(6f))
        }
        // playhead
        drawLine(PinkDeep, Offset(x(t), 0f), Offset(x(t), size.height), 4f)
    }
}

// ------------------------------------------------------------------------------------------------ trays
@Composable
private fun Tray(content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface).padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) { content() }
}

@Composable
private fun Chips(items: List<Pair<String, String>>, sel: String, onPick: (String) -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        items.forEach { (k, l) -> FilterChip(sel == k, { onPick(k) }, label = { Text(l) }) }
    }
}

@Composable
private fun EditTray(m: EdModel, t: Double) {
    val toast = LocalToast.current
    Tray {
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { if (m.end - t >= 3) { m.start = t; m.commit(); toast.show("Clip starts here") } else toast.show("Leave at least 3 seconds") }) { Text("Start here") }
            OutlinedButton(onClick = { if (t - m.start >= 3) { m.end = t; m.commit(); toast.show("Clip ends here") } else toast.show("Leave at least 3 seconds") }) { Text("End here") }
            Button(onClick = { m.cutA = t; toast.show("Cut from ${fmt(t - m.start)}. Play to the end of the part, then Cut to") }) { Text("Cut from") }
            Button(onClick = {
                val a = m.cutA
                if (a == null || t - a < 0.2) toast.show("Press Cut from first, then move the playhead later") else {
                    m.cuts += a to t; m.cutA = null; m.commit(); toast.show("Cut out ${"%.1f".format(t - a)} s")
                }
            }, enabled = m.cutA != null) { Text("Cut to") }
            OutlinedButton(onClick = { m.cuts.clear(); m.cutA = null; m.commit(); toast.show("All cuts removed") }) { Text("Clear cuts") }
        }
        Text("Drag the white handles on the timeline to trim. Cuts show grey.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun CaptionTray(m: EdModel) {
    val cap = m.of("captions"); val hk = m.of("hook")
    var tab by remember { mutableStateOf("style") }
    Tray {
        Chips(listOf("style" to "Style", "words" to "Words", "size" to "Size", "hook" to "Hook"), tab) { tab = it }
        m.rev
        when (tab) {
            "style" -> if (cap != null) Chips(m.styles.keys().asSequence().map { it to (m.styles.optJSONObject(it)?.optString("label") ?: it) }.toList(), cap.optString("style")) { cap.put("style", it).put("hidden", false); m.commit() }
            "words" -> if (cap != null) Chips(listOf("0" to "Auto", "1" to "1", "2" to "2", "3" to "3", "4" to "4", "5" to "5"), cap.optInt("words").toString()) { cap.put("words", it.toInt()); m.commit() }
            "size" -> if (cap != null) Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Size", Modifier.width(60.dp)); Slider(cap.optDouble("scale", 1.0).toFloat(), { cap.put("scale", it.toDouble()); m.rev++ }, Modifier.weight(1f), valueRange = 0.5f..2f, onValueChangeFinished = { m.commit() })
                Switch(!cap.optBoolean("hidden"), { cap.put("hidden", !it); m.commit() })
            }
            "hook" -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                var h by remember { mutableStateOf(m.hook) }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(h, { h = it; m.hook = it; m.rev++ }, Modifier.weight(1f), singleLine = true, label = { Text("Hook") })
                    Spacer(Modifier.width(8.dp))
                    if (hk != null) Switch(!hk.optBoolean("hidden"), { hk.put("hidden", !it); m.commit() })
                }
                if (hk != null) Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("${"%.1f".format(hk.optDouble("secs", 3.5))} s", Modifier.width(60.dp))
                    Slider(hk.optDouble("secs", 3.5).toFloat(), { hk.put("secs", (Math.round(it * 2) / 2.0)); m.rev++ }, Modifier.weight(1f), valueRange = 1f..10f, onValueChangeFinished = { m.commit() })
                }
            }
        }
    }
}

private val COLORS = listOf("#ffffff", "#ffe11a", "#ff3d6e", "#2ee6c8", "#7c5cff", "#ff7a00", "#111111")

@Composable
private fun TextTray(m: EdModel, t: Double) {
    val l = m.selected()?.takeIf { it.optString("kind") == "text" }
    Tray {
        m.rev
        if (l == null) {
            Button(onClick = {
                val nl = JSONObject().put("kind", "text").put("id", m.newId()).put("name", "Text").put("text", "Your text").put("x", 540).put("y", 700)
                    .put("size", 80).put("color", "#ffffff").put("box", false).put("bcolor", "#000000").put("opacity", 1).put("font", "Arial Black")
                    .put("start", max(0.0, t - m.start)).put("end", 0)
                m.layers().put(nl); m.sel = nl.optString("id"); m.commit()
            }, Modifier.fillMaxWidth()) { Icon(Icons.Default.Add, null); Spacer(Modifier.width(6.dp)); Text("Add text at the playhead") }
            Text("Tap a text on the video to change it.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            var txt by remember(l.optString("id")) { mutableStateOf(l.optString("text")) }
            OutlinedTextField(txt, { txt = it; if (it.isNotBlank()) { l.put("text", it); m.rev++ } }, Modifier.fillMaxWidth(), label = { Text("Text") })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                COLORS.forEach { c -> Box(Modifier.size(30.dp).clip(CircleShape).background(Color(android.graphics.Color.parseColor(c))).border(2.dp, if (l.optString("color") == c) Pink else Color.LightGray, CircleShape).clickable { l.put("color", c); m.commit() }) }
                Spacer(Modifier.weight(1f)); Text("Box"); Switch(l.optBoolean("box"), { l.put("box", it); m.commit() })
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Size", Modifier.width(50.dp)); Slider(l.optDouble("size", 70.0).toFloat(), { l.put("size", it.toDouble()); m.rev++ }, Modifier.weight(1f), valueRange = 20f..300f, onValueChangeFinished = { m.commit() })
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { l.put("start", max(0.0, t - m.start)); m.commit() }) { Text("Starts here") }
                OutlinedButton(onClick = { l.put("end", max(0.1, t - m.start)); m.commit() }) { Text("Ends here") }
                TextButton(onClick = { m.sel = null }) { Text("Done") }
            }
        }
    }
}

@Composable
private fun LayoutTray(m: EdModel) {
    val bg = m.scene.optJSONObject("bg") ?: JSONObject().also { m.scene.put("bg", it) }
    Tray {
        m.rev
        Text("Background", style = MaterialTheme.typography.titleSmall)
        Chips(listOf("blur" to "Blurred video", "color" to "Colour", "gradient" to "Gradient"), bg.optString("type", "blur")) { bg.put("type", it); m.commit() }
        if (bg.optString("type", "blur") == "blur") Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Darken", Modifier.width(64.dp)); Slider(bg.optDouble("dim", 0.25).toFloat(), { bg.put("dim", it.toDouble()); m.rev++ }, Modifier.weight(1f), valueRange = 0f..0.8f, onValueChangeFinished = { m.commit() })
        } else Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("#111318", "#000000", "#ffffff", "#3a1c71", "#ff3d6e", "#2ee6c8").forEach { c -> Box(Modifier.size(30.dp).clip(CircleShape).background(Color(android.graphics.Color.parseColor(c))).border(2.dp, Color.LightGray, CircleShape).clickable { bg.put("color", c); m.commit() }) }
        }
        OutlinedButton(onClick = {
            val l = JSONObject().put("kind", "video").put("id", m.newId()).put("name", "Window").put("role", "cam").put("x", 290).put("y", 1180).put("w", 500).put("h", 500)
                .put("crop", JSONArray(listOf(0.0, 0.0, 0.3, 0.53))).put("shape", "round").put("radius", 0.12).put("border", 6).put("bcolor", "#ffffff").put("keys", JSONArray()).put("kmode", "smooth")
            m.fitCrop(l); m.layers().put(l); m.sel = l.optString("id"); m.commit()
        }, Modifier.fillMaxWidth()) { Icon(Icons.Default.Add, null); Spacer(Modifier.width(6.dp)); Text("Add a video window (facecam)") }
        Text("Tap a window on the video to crop, reshape or resize it.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun ObjectTray(m: EdModel, l: JSONObject, t: Double, cropMode: Boolean, setCrop: (Boolean) -> Unit, onDone: () -> Unit) {
    val vid = l.optString("kind") == "video"
    Tray {
        m.rev
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(if (vid) l.optString("name", "Window") else "Image", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
            if (vid) FilterChip(cropMode, { setCrop(!cropMode) }, label = { Text("Crop") }, leadingIcon = { Icon(Icons.Default.Crop, null, Modifier.size(16.dp)) })
            TextButton(onClick = onDone) { Text("Done") }
        }
        Chips(listOf("rect" to "Square", "round" to "Rounded", "pill" to "Pill", "circle" to "Circle"), l.optString("shape", "rect")) { l.put("shape", it); m.commit() }
        if (vid) Chips(listOf("full" to "Fill screen", "9:16" to "9:16", "16:9" to "16:9", "1:1" to "1:1", "4:5" to "4:5"), "") { r ->
            if (r == "full") l.put("x", 0).put("y", 0).put("w", 1080).put("h", 1920) else {
                val (a, b) = r.split(":").map { it.toDouble() }; val cx = l.optDouble("x") + l.optDouble("w") / 2; val cy = l.optDouble("y") + l.optDouble("h") / 2
                var w = l.optDouble("w"); var h = w * b / a; if (h > 1920) { h = 1920.0; w = h * a / b }; if (w > 1080) { w = 1080.0; h = w * b / a }
                l.put("w", w).put("h", h).put("x", cx - w / 2).put("y", cy - h / 2)
            }
            m.fitCrop(l); m.commit()
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Opacity", Modifier.width(64.dp)); Slider(l.optDouble("opacity", 1.0).toFloat(), { l.put("opacity", it.toDouble()); m.rev++ }, Modifier.weight(1f), valueRange = 0.05f..1f, onValueChangeFinished = { m.commit() })
        }
        if (vid) Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Border", Modifier.width(64.dp)); Slider(l.optDouble("border", 0.0).toFloat(), { l.put("border", it.toInt()); m.rev++ }, Modifier.weight(1f), valueRange = 0f..30f, onValueChangeFinished = { m.commit() })
        }
        if (vid && cropMode) Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            val keys = l.optJSONArray("keys") ?: JSONArray()
            OutlinedButton(onClick = { l.put("crop", JSONArray(m.cropAt(l, t).toList())); m.setKey(l, t); m.commit() }) { Text("◆ Keyframe") }
            Chips(listOf("smooth" to "Smooth", "cut" to "Jump"), l.optString("kmode", "smooth")) { l.put("kmode", it); m.commit() }
            Text("${keys.length()} keys", style = MaterialTheme.typography.bodySmall)
            if (keys.length() > 0) TextButton(onClick = { l.put("crop", JSONArray(m.cropAt(l, t).toList())); l.put("keys", JSONArray()); m.commit() }) { Text("Clear") }
        }
        Text(if (vid) "Drag the window to move it, pinch to resize. Turn on Crop to move the picture inside it." else "Drag to move, pinch to resize.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun ImageTray(app: AppModel, m: EdModel, t: Double) {
    val ctx = LocalContext.current
    var files by remember { mutableStateOf<List<String>?>(null) }
    suspend fun reload() { files = runCatching { app.api.get("/api/assets/images").optJSONArray("files")?.strings() ?: emptyList() }.getOrDefault(emptyList()) }
    val scope = rememberCoroutineScope()
    fun add(f: String) {
        val l = JSONObject().put("kind", "image").put("id", m.newId()).put("name", "Image").put("file", f).put("x", 540).put("y", 700).put("w", 400)
            .put("opacity", 1).put("start", 0).put("end", 0).put("shape", "rect").put("crop", JSONArray(listOf(0, 0, 1, 1)))
        m.layers().put(l); m.sel = l.optString("id"); m.commit()
    }
    val upload = com.chipper.studio.net.rememberUploader(app, "image/*", kindFor = { _, _ -> "images" }) { done ->
        done.firstOrNull()?.optString("file")?.let { add(it) }
        scope.launch { reload() }
    }
    LaunchedEffect(Unit) { reload() }
    Tray {
        Text("Tap an image to add it. Uploads are kept on your PC in uploads\\images.", style = MaterialTheme.typography.titleSmall)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.size(72.dp).clip(RoundedCornerShape(10.dp)).background(Pink).clickable { upload() }, contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) { Icon(Icons.Default.Upload, null, tint = Color.White); Text("Upload", color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold) }
            }
            if (files == null) repeat(4) { com.chipper.studio.ui.Shimmer(Modifier.size(72.dp), 10.dp) }
            (files ?: emptyList()).forEach { f ->
                coil.compose.AsyncImage(
                    ImageRequest.Builder(ctx).data(app.api.url("/assets/${enc(f)}")).addHeader("Cookie", app.api.cookieHeader()).build(), f,
                    Modifier.size(72.dp).clip(RoundedCornerShape(10.dp)).background(Color.DarkGray).clickable { add(f) },
                    contentScale = androidx.compose.ui.layout.ContentScale.Crop)
            }
        }
    }
}

@Composable
private fun SoonTray(what: String, onWeb: () -> Unit) {
    Tray {
        Text("$what is coming to the app editor next. Until then, the full web editor has it.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedButton(onClick = onWeb, Modifier.fillMaxWidth()) { Text("Open the web editor") }
    }
}
