package com.chipper.studio.editor

import android.graphics.Bitmap
import android.util.Base64
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Redo
import androidx.compose.material.icons.automirrored.filled.Undo
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Crop
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.EmojiEmotions
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.TextFields
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
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
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import coil.ImageLoader
import coil.request.ImageRequest
import coil.request.SuccessResult
import com.chipper.studio.AppModel
import com.chipper.studio.net.objects
import com.chipper.studio.net.rememberUploader
import com.chipper.studio.net.strings
import com.chipper.studio.ui.Bad
import com.chipper.studio.ui.LocalToast
import com.chipper.studio.ui.Pink
import com.chipper.studio.ui.PinkDeep
import com.chipper.studio.ui.Shimmer
import com.chipper.studio.ui.enc
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/** Design state of the thumbnail editor, with undo. */
class CoverModel(d0: JSONObject) {
    var d: JSONObject = d0
    var sel by mutableStateOf<String?>(null)
    var crop by mutableStateOf(false)
    var rev by mutableIntStateOf(0)
    var dirty = false
    private val hist = mutableListOf<String>(); private var hi = -1
    fun layers(): JSONArray = d.optJSONArray("layers") ?: JSONArray().also { d.put("layers", it) }
    fun list(): List<JSONObject> = layers().objects()
    fun selected(): JSONObject? = list().firstOrNull { it.optString("id") == sel }
    fun bg(): JSONObject = d.optJSONObject("bg") ?: CoverDraw.defaults().getJSONObject("bg").also { d.put("bg", it) }
    fun touch() { rev++ }
    fun commit() {
        val s = d.toString(); if (hi >= 0 && hist[hi] == s) { rev++; return }
        while (hist.size > hi + 1) hist.removeAt(hist.size - 1)
        hist += s; if (hist.size > 80) hist.removeAt(0); hi = hist.size - 1
        if (hist.size > 1) dirty = true
        rev++
    }
    val canUndo get() = hi > 0
    val canRedo get() = hi < hist.size - 1
    private fun restore(i: Int) { hi = i; d = JSONObject(hist[i]); if (selected() == null) { sel = null; crop = false }; dirty = true; rev++ }
    fun undo() { if (canUndo) restore(hi - 1) }
    fun redo() { if (canRedo) restore(hi + 1) }
    fun add(l: JSONObject) { layers().put(l); sel = l.optString("id"); crop = false; commit() }
    fun remove(l: JSONObject) {
        val a = JSONArray(); list().filter { it !== l && it.optString("id") != l.optString("id") }.forEach { a.put(it) }
        d.put("layers", a); sel = null; crop = false; commit()
    }
    fun move(l: JSONObject, by: Int) {
        val ls = list().toMutableList(); val i = ls.indexOfFirst { it.optString("id") == l.optString("id") }; val j = (i + by).coerceIn(0, ls.size - 1)
        if (i < 0 || i == j) return
        val x = ls.removeAt(i); ls.add(j, x); d.put("layers", JSONArray().also { a -> ls.forEach { a.put(it) } }); commit()
    }
    fun local(l: JSONObject, x: Float, y: Float): FloatArray {
        val a = -(l.optDouble("rot", 0.0) * PI / 180).toFloat(); val dx = x - l.optDouble("x").toFloat(); val dy = y - l.optDouble("y").toFloat()
        return floatArrayOf(dx * cos(a) - dy * sin(a), dx * sin(a) + dy * cos(a))
    }
    fun hit(x: Float, y: Float): JSONObject? = list().reversed().firstOrNull { l ->
        if (l.optBoolean("hidden")) false else { val s = CoverDraw.size(l); val u = local(l, x, y); abs(u[0]) <= s[0] / 2 + 10 && abs(u[1]) <= s[1] / 2 + 10 }
    }
    fun scale(l: JSONObject, f: Float) {
        if (l.optString("kind") == "photo") { l.put("w", (l.optDouble("w") * f).coerceIn(60.0, 3000.0)); l.put("h", (l.optDouble("h") * f).coerceIn(60.0, 4000.0)) }
        else l.put("size", (l.optDouble("size", 120.0) * f).coerceIn(20.0, 1000.0))
    }
}

private enum class CTool(val label: String, val icon: ImageVector) {
    LAYOUTS("Layouts", Icons.Default.Dashboard), FRAME("Frame", Icons.Default.Movie), PHOTOS("Photos", Icons.Default.Image),
    TEXT("Text", Icons.Default.TextFields), STICKERS("Stickers", Icons.Default.EmojiEmotions), BG("Background", Icons.Default.Palette), LAYERS("Layers", Icons.Default.Layers)
}

private val SWATCHES = listOf("#ffffff", "#000000", "#ffe11a", "#e8173c", "#ff3d6e", "#ff8a00", "#2ee6c8", "#1877f2", "#9146ff", "#9aa1ad")
private val FONTS = listOf("Arial Black", "Impact", "Anton", "Bebas Neue", "Montserrat", "Pricedown Black", "Poppins", "Arial")

/** The thumbnail editor: a free 1080x1920 canvas like the video editor, drawn and saved from the phone. */
@OptIn(UnstableApi::class)
@Composable
fun CoverEditorScreen(app: AppModel, name: String, onClose: () -> Unit, onSaved: () -> Unit = {}) {
    val ctx = LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var m by remember { mutableStateOf<CoverModel?>(null) }
    var hook by remember { mutableStateOf("NO WAY") }
    var dur by remember { mutableStateOf(10.0) }
    var err by remember { mutableStateOf<String?>(null) }
    val images = remember { mutableStateMapOf<String, Bitmap>() }
    val loader = remember { ImageLoader(ctx) }
    val path = "/api/clips/${enc(name)}"

    suspend fun bitmap(src: String): Bitmap? {
        if (src.isEmpty()) return null
        images[src]?.let { return it }
        val r = loader.execute(ImageRequest.Builder(ctx).data(app.api.url(src)).addHeader("Cookie", app.api.cookieHeader()).allowHardware(false).size(2048).build())
        return ((r as? SuccessResult)?.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap?.also { images[src] = it }
    }
    suspend fun grab(t: Double): Triple<String, Int, Int>? = runCatching {
        val j = app.api.post("$path/thumb/photo", JSONObject().put("t", t))
        Triple("/thumb/${enc(j.optString("file"))}", j.optInt("w"), j.optInt("h"))
    }.onFailure { toast.show(it.message ?: "Could not grab that frame") }.getOrNull()

    LaunchedEffect(name) {
        runCatching {
            val j = app.api.get("$path/thumb")
            dur = j.optDouble("duration", 10.0)
            hook = j.optString("hook").ifBlank { "NO WAY" }
            val des = j.optJSONObject("design")
            val d = if (des != null && des.optInt("v") == 2) des else {
                val d = CoverDraw.defaults(); val t = (dur * 0.35 * 100).roundToInt() / 100.0; d.put("t", t)
                val f = grab(t)
                if (f != null) d.getJSONObject("bg").put("src", f.first).put("nw", f.second).put("nh", f.third)
                d.put("layers", JSONArray().also { a -> CoverDraw.LAYOUTS.getValue("hook").second(f?.first ?: "", f?.second ?: 16, f?.third ?: 9, hook).forEach { a.put(it) } }).put("layout", "hook")
                d
            }
            CoverModel(d).also { it.commit() }
        }.onSuccess { m = it }.onFailure { err = it.message ?: "Could not open the thumbnail" }
    }
    // load every picture the design uses
    val mm = m
    LaunchedEffect(mm?.rev) {
        val c = mm ?: return@LaunchedEffect
        (listOf(c.bg().optString("src")) + c.list().filter { it.optString("kind") == "photo" }.map { it.optString("src") }).filter { it.isNotEmpty() && it !in images }.distinct().forEach { bitmap(it) }
    }

    if (mm == null) {
        Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).statusBarsPadding().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) { IconButton(onClick = onClose) { Icon(Icons.Default.Close, "Close") }; Text("Thumbnail", style = MaterialTheme.typography.titleMedium) }
            if (err != null) Text(err!!, color = Bad) else {
                Shimmer(Modifier.fillMaxWidth(0.7f).aspectRatio(9f / 16f).align(Alignment.CenterHorizontally), 16.dp)
                Shimmer(Modifier.fillMaxWidth().height(64.dp))
            }
        }
        BackHandler(onBack = onClose)
        return
    }
    CoverEditor(app, mm, name, hook, dur, images, { bitmap(it) }, { grab(it) }, onClose, onSaved)
}

@OptIn(UnstableApi::class)
@Composable
private fun CoverEditor(
    app: AppModel, m: CoverModel, name: String, hook: String, dur: Double, images: Map<String, Bitmap>,
    bitmap: suspend (String) -> Bitmap?, grab: suspend (Double) -> Triple<String, Int, Int>?,
    onClose: () -> Unit, onSaved: () -> Unit,
) {
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    var tool by remember { mutableStateOf<CTool?>(CTool.LAYOUTS) }
    var askClose by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    val path = "/api/clips/${enc(name)}"
    BackHandler { if (m.crop) m.crop = false else if (m.sel != null) m.sel = null else if (m.dirty) askClose = true else onClose() }

    fun save() {
        saving = true
        scope.launch {
            runCatching {
                val keep = m.sel; m.sel = null
                val jpg = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) { CoverDraw.toJpeg(m.d, images) }
                m.sel = keep
                app.api.post("$path/cover/design", JSONObject().put("image", "data:image/jpeg;base64," + Base64.encodeToString(jpg, Base64.NO_WRAP)).put("design", JSONObject(m.d.toString())))
            }.onSuccess { toast.show("Thumbnail saved"); app.coverV[name] = System.currentTimeMillis(); m.dirty = false; onSaved(); onClose() }
                .onFailure { toast.show(it.message ?: "Could not save the thumbnail") }
            saving = false
        }
    }

    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).statusBarsPadding().navigationBarsPadding().imePadding()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { if (m.dirty) askClose = true else onClose() }) { Icon(Icons.Default.Close, "Close") }
            IconButton(onClick = { m.undo() }, enabled = m.rev >= 0 && m.canUndo) { Icon(Icons.AutoMirrored.Filled.Undo, "Undo") }
            IconButton(onClick = { m.redo() }, enabled = m.rev >= 0 && m.canRedo) { Icon(Icons.AutoMirrored.Filled.Redo, "Redo") }
            Spacer(Modifier.weight(1f))
            if (m.rev >= 0) m.selected()?.let { l -> IconButton(onClick = { m.remove(l) }) { Icon(Icons.Default.Delete, "Delete the selected layer", tint = Bad) } }
            Button(onClick = { save() }, enabled = !saving, shape = RoundedCornerShape(12.dp)) {
                if (saving) CircularProgressIndicator(Modifier.size(18.dp), color = Color.White, strokeWidth = 2.dp)
                else { Icon(Icons.Default.Save, null, Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)); Text("Save", fontWeight = FontWeight.Bold) }
            }
        }

        // the cover
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth().padding(vertical = 4.dp), contentAlignment = Alignment.Center) {
            val hW = min(maxWidth.value, maxHeight.value * 9 / 16)
            val k = with(LocalDensity.current) { hW.dp.toPx() } / CoverDraw.CW
            val kNow by rememberUpdatedState(k)
            Box(Modifier.width(hW.dp).aspectRatio(9f / 16f).clip(RoundedCornerShape(14.dp)).background(Color.Black)) {
                Canvas(Modifier.fillMaxSize().pointerInput(m) { coverGestures(m) { kNow } }) {
                    m.rev; images.size
                    drawIntoCanvas { c -> val nc = c.nativeCanvas; nc.save(); nc.scale(k, k); CoverDraw.render(nc, m.d, images, m.selected(), m.crop, k); nc.restore() }
                }
                if (m.crop) Text("Crop: drag to move, pinch to zoom", Modifier.align(Alignment.TopCenter).padding(8.dp).clip(RoundedCornerShape(8.dp)).background(Color.Black.copy(alpha = 0.6f)).padding(horizontal = 10.dp, vertical = 4.dp), color = Color(0xFFFFD400), fontSize = 12.sp)
            }
        }

        val sel = if (m.rev >= 0) m.selected() else null
        Box(Modifier.fillMaxWidth().heightIn(max = 300.dp)) {
            if (sel != null) PropsTray(m, sel, images)
            else when (tool) {
                CTool.LAYOUTS -> LayoutsTray(m, hook, images, bitmap, grab)
                CTool.FRAME -> FrameTray(app, m, name, dur, grab)
                CTool.PHOTOS -> PhotosTray(app, m, path, bitmap, grab)
                CTool.TEXT -> TextTray(m, hook)
                CTool.STICKERS -> StickerTray(m)
                CTool.BG -> BgTray(app, m, path)
                CTool.LAYERS -> LayersTray(m)
                null -> {}
            }
        }
        Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface).horizontalScroll(rememberScrollState()).padding(horizontal = 4.dp, vertical = 4.dp)) {
            CTool.entries.forEach { tt ->
                val on = tool == tt && sel == null
                Column(Modifier.width(66.dp).clip(RoundedCornerShape(12.dp)).background(if (on) Pink.copy(alpha = 0.14f) else Color.Transparent)
                    .clickable { m.sel = null; m.crop = false; tool = if (tool == tt) null else tt }.padding(vertical = 6.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(tt.icon, null, tint = if (on) PinkDeep else MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(tt.label, fontSize = 10.5.sp, color = if (on) PinkDeep else MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
    if (askClose) AlertDialog(onDismissRequest = { askClose = false }, title = { Text("Close without saving the thumbnail?") },
        confirmButton = { TextButton(onClick = { askClose = false; onClose() }) { Text("Close", color = Bad) } },
        dismissButton = { TextButton(onClick = { askClose = false; save() }) { Text("Save") } })
}

/** Tap to select, drag to move, corner dot to resize, top dot to rotate, two fingers to pinch and twist. Crop mode moves the picture inside a photo. */
private suspend fun androidx.compose.ui.input.pointer.PointerInputScope.coverGestures(m: CoverModel, k: () -> Float) {
    awaitEachGesture {
        val down = awaitFirstDown(requireUnconsumed = false)
        val kk = k()
        fun cv(o: Offset) = floatArrayOf(o.x / kk, o.y / kk)
        val p0 = cv(down.position)
        val s = m.selected()
        var mode = ""; var target: JSONObject? = null
        if (s != null && m.crop && s.optString("kind") == "photo") { mode = "crop"; target = s }
        else if (s != null) {
            val sz = CoverDraw.size(s); val u = m.local(s, p0[0], p0[1]); val r = 30 / kk
            if (hypot(u[0] - sz[0] / 2, u[1] - sz[1] / 2) < r) { mode = "scale"; target = s }
            else if (hypot(u[0], u[1] + sz[1] / 2 + 46 / kk) < r) { mode = "rotate"; target = s }
        }
        if (target == null) {
            val h = m.hit(p0[0], p0[1])
            if (h != null) { m.sel = h.optString("id"); mode = "move"; target = h } else { m.sel = null; m.crop = false }
            m.touch()
        }
        val t = target ?: return@awaitEachGesture
        var rx = t.optDouble("x").toFloat(); var ry = t.optDouble("y").toFloat(); var rr = t.optDouble("rot", 0.0).toFloat()
        var moved = false
        while (true) {
            val ev = awaitPointerEvent()
            val pts = ev.changes.filter { it.pressed }
            if (pts.isEmpty()) break
            if (pts.size >= 2) {
                val a = cv(pts[0].position); val b = cv(pts[1].position); val pa = cv(pts[0].previousPosition); val pb = cv(pts[1].previousPosition)
                val d = hypot(a[0] - b[0], a[1] - b[1]); val pd = max(1f, hypot(pa[0] - pb[0], pa[1] - pb[1])); val f = d / pd
                if (m.crop && t.optString("kind") == "photo") { val c = t.getJSONObject("crop"); c.put("z", (c.optDouble("z", 1.0) * f).coerceIn(1.0, 6.0)) }
                else {
                    m.scale(t, f)
                    rr += ((atan2(b[1] - a[1], b[0] - a[0]) - atan2(pb[1] - pa[1], pb[0] - pa[0])) * 180 / PI).toFloat()
                    t.put("rot", rr.roundToInt())
                }
            } else {
                val c = pts[0]; val p = cv(c.position); val q = cv(c.previousPosition)
                val cx = t.optDouble("x").toFloat(); val cy = t.optDouble("y").toFloat()
                when (mode) {
                    "move" -> {
                        rx += p[0] - q[0]; ry += p[1] - q[1]
                        t.put("x", (if (abs(rx - CoverDraw.CW / 2) < 18) CoverDraw.CW / 2 else rx).roundToInt())
                        t.put("y", (if (abs(ry - CoverDraw.CH / 2) < 18) CoverDraw.CH / 2 else ry).roundToInt())
                    }
                    "scale" -> {
                        val sz = CoverDraw.size(t); val u = m.local(t, p[0], p[1])
                        val target2 = hypot(u[0], u[1]); val now = hypot(sz[0] / 2, sz[1] / 2)
                        if (now > 1) m.scale(t, target2 / now)
                    }
                    "rotate" -> {
                        rr += ((atan2(p[1] - cy, p[0] - cx) - atan2(q[1] - cy, q[0] - cx)) * 180 / PI).toFloat()
                        val mod = ((rr % 90) + 90) % 90
                        t.put("rot", if (mod < 4 || mod > 86) (rr / 90).roundToInt() * 90 else rr.roundToInt())
                    }
                    "crop" -> {
                        val sz = CoverDraw.size(t); val nw = t.optDouble("nw", 1600.0).toFloat(); val nh = t.optDouble("nh", 900.0).toFloat()
                        val cr = t.getJSONObject("crop"); val sc = max(sz[0] / nw, sz[1] / nh) * cr.optDouble("z", 1.0).toFloat()
                        val ex = nw * sc - sz[0]; val ey = nh * sc - sz[1]
                        val a = -(t.optDouble("rot", 0.0) * PI / 180).toFloat(); val dx = p[0] - q[0]; val dy = p[1] - q[1]
                        val du = dx * cos(a) - dy * sin(a); val dv = dx * sin(a) + dy * cos(a)
                        cr.put("ox", if (ex > 1) (cr.optDouble("ox", 0.5) - (if (t.optBoolean("flip")) -du else du) / ex).coerceIn(0.0, 1.0) else 0.5)
                        cr.put("oy", if (ey > 1) (cr.optDouble("oy", 0.5) - dv / ey).coerceIn(0.0, 1.0) else 0.5)
                    }
                }
            }
            pts.forEach { it.consume() }
            moved = true; m.touch()
        }
        if (moved) m.commit()
    }
}

// ------------------------------------------------------------------ small pieces
@Composable
private fun CTray(content: @Composable () -> Unit) {
    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface).verticalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) { content() }
}

@Composable
private fun Sl(label: String, v: Double, a: Float, b: Float, on: (Double) -> Unit, done: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.width(92.dp), fontSize = 13.sp)
        Slider(v.toFloat().coerceIn(a, b), { on(it.toDouble()) }, Modifier.weight(1f), valueRange = a..b, onValueChangeFinished = done)
    }
}

@Composable
private fun Swatches(cur: String, onPick: (String) -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        SWATCHES.forEach { c ->
            Box(Modifier.size(30.dp).clip(CircleShape).background(Color(android.graphics.Color.parseColor(c)))
                .border(if (cur.equals(c, true)) 3.dp else 1.dp, if (cur.equals(c, true)) Pink else Color.Gray.copy(alpha = 0.4f), CircleShape).clickable { onPick(c) })
        }
    }
}

@Composable
private fun Chips(opts: List<Pair<String, String>>, cur: String, on: (String) -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        opts.forEach { (k, l) -> FilterChip(cur == k, { on(k) }, label = { Text(l) }) }
    }
}

@Composable
private fun Big(label: String, icon: ImageVector, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Box(modifier.height(48.dp).clip(RoundedCornerShape(12.dp)).background(Pink).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Row(verticalAlignment = Alignment.CenterVertically) { Icon(icon, null, tint = Color.White); Spacer(Modifier.width(6.dp)); Text(label, color = Color.White, fontWeight = FontWeight.Bold) }
    }
}

// ------------------------------------------------------------------ trays
@Composable
private fun LayoutsTray(m: CoverModel, hook: String, images: Map<String, Bitmap>, bitmap: suspend (String) -> Bitmap?, grab: suspend (Double) -> Triple<String, Int, Int>?) {
    val scope = rememberCoroutineScope()
    CTray {
        Text("A layout replaces the photos, text and stickers. Undo brings yours back.", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            val bg = m.bg()
            CoverDraw.LAYOUTS.forEach { (k, v) ->
                val mini = remember(k, bg.optString("src"), hook) {
                    JSONObject(m.d.toString()).also { d -> d.getJSONObject("bg").put("blur", 0); d.put("layers", JSONArray().also { a -> v.second(bg.optString("src"), bg.optInt("nw", 16), bg.optInt("nh", 9), hook).forEach { a.put(it) } }) }
                }
                Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.clickable {
                    scope.launch {
                        var src = m.bg().optString("src"); var w = m.bg().optInt("nw", 16); var h = m.bg().optInt("nh", 9)
                        if (src.isEmpty()) grab(m.d.optDouble("t", 0.0))?.let { f -> src = f.first; w = f.second; h = f.third; m.bg().put("src", src).put("nw", w).put("nh", h); bitmap(src) }
                        val b = m.bg()
                        when (k) { "split" -> b.put("blur", 0).put("bright", 1.0); "card", "versus" -> b.put("blur", 22).put("bright", 0.7); else -> b.put("blur", 0).put("bright", 1.05) }
                        m.d.put("layers", JSONArray().also { a -> v.second(src, w, h, hook).forEach { a.put(it) } }).put("layout", k)
                        m.sel = null; m.commit()
                    }
                }) {
                    Box(Modifier.width(58.dp).aspectRatio(9f / 16f).clip(RoundedCornerShape(8.dp)).border(2.dp, if (m.d.optString("layout") == k) Pink else Color.Transparent, RoundedCornerShape(8.dp))) {
                        Canvas(Modifier.fillMaxSize()) { images.size; val s = size.width / CoverDraw.CW; drawIntoCanvas { c -> c.nativeCanvas.save(); c.nativeCanvas.scale(s, s); CoverDraw.render(c.nativeCanvas, mini, images); c.nativeCanvas.restore() } }
                    }
                    Text(v.first, fontSize = 10.5.sp, maxLines = 1)
                }
            }
        }
    }
}

@OptIn(UnstableApi::class)
@Composable
private fun FrameTray(app: AppModel, m: CoverModel, name: String, dur: Double, grab: suspend (Double) -> Triple<String, Int, Int>?) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var t by remember { mutableStateOf(m.d.optDouble("t", 0.0)) }
    var busy by remember { mutableStateOf(false) }
    val player = remember {
        ExoPlayer.Builder(ctx).setMediaSourceFactory(DefaultMediaSourceFactory(OkHttpDataSource.Factory(app.api.http))).build().apply {
            setMediaItem(MediaItem.fromUri(app.api.url("/media/${enc(name)}.mp4"))); volume = 0f; prepare(); seekTo((t * 1000).toLong())
        }
    }
    androidx.compose.runtime.DisposableEffect(Unit) { onDispose { player.release() } }
    CTray {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            AndroidView({ c -> PlayerView(c).apply { this.player = player; useController = false; resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT; setShutterBackgroundColor(android.graphics.Color.BLACK) } },
                Modifier.width(68.dp).aspectRatio(9f / 16f).clip(RoundedCornerShape(8.dp)))
            Column(Modifier.weight(1f)) {
                Text("Pick the moment (${"%.1f".format(t)} s)", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                Slider(t.toFloat(), { t = it.toDouble(); player.seekTo((t * 1000).toLong()) }, valueRange = 0f..max(0.5f, dur.toFloat()))
                Text("Frames come from the stream itself, without captions.", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { busy = true; scope.launch { grab(t)?.let { f -> m.bg().put("src", f.first).put("nw", f.second).put("nh", f.third).put("kind", "frame"); m.d.put("t", t); m.commit() }; busy = false } },
                enabled = !busy, modifier = Modifier.weight(1f)) { Text("Use as background") }
            OutlinedButton(onClick = { busy = true; scope.launch { grab(t)?.let { f -> m.add(CoverDraw.newPhoto(f.first, f.second, f.third)) }; busy = false } },
                enabled = !busy, modifier = Modifier.weight(1f)) { Text("Add as a photo") }
        }
    }
}

@Composable
private fun PhotosTray(app: AppModel, m: CoverModel, path: String, bitmap: suspend (String) -> Bitmap?, grab: suspend (Double) -> Triple<String, Int, Int>?) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var lib by remember { mutableStateOf<List<String>?>(null) }
    LaunchedEffect(Unit) { lib = runCatching { app.api.get("/api/assets/images").optJSONArray("files")?.strings() ?: emptyList() }.getOrDefault(emptyList()) }
    val upload = rememberUploader(app, "image/*", path = "$path/thumb/photo") { done ->
        done.forEachIndexed { i, j -> m.add(CoverDraw.newPhoto("/thumb/${enc(j.optString("file"))}", j.optInt("w"), j.optInt("h"), 540.0 + i * 40, 1100.0 + i * 60)) }
    }
    CTray {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Big("Upload photos", Icons.Default.Upload, Modifier.weight(1f)) { upload() }
            OutlinedButton(onClick = { scope.launch { grab(m.d.optDouble("t", 0.0))?.let { f -> m.add(CoverDraw.newPhoto(f.first, f.second, f.third)) } } }, Modifier.weight(1f).height(48.dp)) {
                Icon(Icons.Default.Movie, null, Modifier.size(18.dp)); Spacer(Modifier.width(6.dp)); Text("Frame from clip")
            }
        }
        Text("Your images", style = MaterialTheme.typography.titleSmall)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (lib == null) repeat(4) { Shimmer(Modifier.size(68.dp), 10.dp) }
            lib?.forEach { f ->
                val src = "/assets/${enc(f)}"
                coil.compose.AsyncImage(ImageRequest.Builder(ctx).data(app.api.url(src)).addHeader("Cookie", app.api.cookieHeader()).build(), f,
                    Modifier.size(68.dp).clip(RoundedCornerShape(10.dp)).background(Color.DarkGray).clickable {
                        scope.launch { val b = bitmap(src); m.add(CoverDraw.newPhoto(src, b?.width ?: 1, b?.height ?: 1, w = 520.0, shape = "rect", border = 0)) }
                    }, contentScale = ContentScale.Crop)
            }
            if (lib?.isEmpty() == true) Text("Images you upload in the video editor show here too.", fontSize = 12.sp)
        }
        Text("Drag photos on the cover, use the corner dot to resize, the top dot to rotate. Tap Crop to choose the part that shows.", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun TextTray(m: CoverModel, hook: String) {
    CTray {
        Big("Add text", Icons.Default.TextFields, Modifier.fillMaxWidth()) { m.add(CoverDraw.newText(hook)) }
        Text("Text styles: tap to add text in a style", style = MaterialTheme.typography.titleSmall)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            CoverDraw.LOOKS.forEach { (k, o) -> LookTile(o) { m.add(CoverDraw.newText(hook, k)) } }
        }
        Text("Quick words", style = MaterialTheme.typography.titleSmall)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("NO WAY", "GONE WRONG", "HE DID IT", "INSANE", "1 IN A MILLION", "WAIT FOR IT", "\$1,000,000", "CAUGHT ON STREAM").forEach { w ->
                FilterChip(false, { m.add(CoverDraw.newText(w, "beast", y = 760.0)) }, label = { Text(w) })
            }
        }
    }
}

@Composable
private fun LookTile(o: CoverDraw.Look, on: Boolean = false, onClick: () -> Unit) {
    val box = if (o.box.isEmpty()) Color(0xFF2A2D36) else Color(android.graphics.Color.parseColor(o.box))
    Box(Modifier.width(96.dp).height(52.dp).clip(RoundedCornerShape(10.dp)).background(box).border(2.dp, if (on) Pink else Color.Transparent, RoundedCornerShape(10.dp)).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Text(o.label, color = Color(android.graphics.Color.parseColor(o.color)), fontWeight = FontWeight.Black, fontSize = 12.sp, maxLines = 1)
    }
}

@Composable
private fun StickerTray(m: CoverModel) {
    CTray {
        Text("Shapes", style = MaterialTheme.typography.titleSmall)
        Chips(CoverDraw.SHAPES, "") { m.add(CoverDraw.newSticker(it)) }
        Text("Emoji", style = MaterialTheme.typography.titleSmall)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            CoverDraw.EMOJI.forEach { e -> Box(Modifier.size(48.dp).clip(RoundedCornerShape(10.dp)).clickable { m.add(CoverDraw.newSticker(e)) }, contentAlignment = Alignment.Center) { Text(e, fontSize = 26.sp) } }
        }
    }
}

@Composable
private fun BgTray(app: AppModel, m: CoverModel, path: String) {
    val bg = m.bg()
    val upload = rememberUploader(app, "image/*", path = "$path/thumb/photo") { done ->
        done.firstOrNull()?.let { j -> bg.put("src", "/thumb/${enc(j.optString("file"))}").put("nw", j.optInt("w")).put("nh", j.optInt("h")).put("kind", "frame"); m.commit() }
    }
    CTray {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Chips(listOf("frame" to "Picture", "color" to "Colour"), bg.optString("kind", "frame")) { bg.put("kind", it); m.commit() }
            Spacer(Modifier.weight(1f))
            OutlinedButton(onClick = upload) { Icon(Icons.Default.Upload, null, Modifier.size(16.dp)); Spacer(Modifier.width(4.dp)); Text("Upload") }
        }
        if (bg.optString("kind") == "color") Swatches(bg.optString("color")) { bg.put("color", it); m.commit() }
        else {
            val set = { key: String -> { v: Double -> bg.put(key, v); m.touch() } }
            Sl("Zoom", bg.optDouble("z", 1.0), 1f, 3f, set("z")) { m.commit() }
            Sl("Left / right", bg.optDouble("ox", 0.5), 0f, 1f, set("ox")) { m.commit() }
            Sl("Up / down", bg.optDouble("oy", 0.5), 0f, 1f, set("oy")) { m.commit() }
            Sl("Brightness", bg.optDouble("bright", 1.0), 0.4f, 1.6f, set("bright")) { m.commit() }
            Sl("Contrast", bg.optDouble("contrast", 1.0), 0.6f, 1.8f, set("contrast")) { m.commit() }
            Sl("Colour", bg.optDouble("sat", 1.0), 0f, 2f, set("sat")) { m.commit() }
            Sl("Blur", bg.optDouble("blur", 0.0), 0f, 40f, { bg.put("blur", Math.round(it).toDouble()); m.touch() }) { m.commit() }
            Sl("Dark edges", bg.optDouble("vignette", 0.0), 0f, 0.9f, set("vignette")) { m.commit() }
        }
    }
}

@Composable
private fun LayersTray(m: CoverModel) {
    CTray {
        Text("Top of the list is in front. Tap one to select it.", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        m.list().reversed().forEach { l ->
            Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(MaterialTheme.colorScheme.surfaceVariant).clickable { m.sel = l.optString("id") }.padding(horizontal = 10.dp, vertical = 2.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Text(when (l.optString("kind")) { "text" -> l.optString("text").take(30); "sticker" -> CoverDraw.SHAPES.toMap()[l.optString("t")] ?: l.optString("t"); else -> "Photo" },
                    Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold)
                TextButton(onClick = { m.move(l, 1) }) { Text("▲") }
                TextButton(onClick = { m.move(l, -1) }) { Text("▼") }
                IconButton(onClick = { l.put("hidden", !l.optBoolean("hidden")); m.commit() }) { Icon(if (l.optBoolean("hidden")) Icons.Default.VisibilityOff else Icons.Default.Visibility, "Show or hide") }
                IconButton(onClick = { m.remove(l) }) { Icon(Icons.Default.Delete, "Delete", tint = Bad) }
            }
        }
        Text("Background", Modifier.padding(start = 10.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** Settings for the selected photo, text or sticker. */
@Composable
private fun PropsTray(m: CoverModel, l: JSONObject, images: Map<String, Bitmap>) {
    val kind = l.optString("kind")
    val live = { key: String -> { v: Double -> l.put(key, v); m.touch() } }
    var words by remember(l.optString("id")) { mutableStateOf(l.optString("text")) }
    CTray {
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(when (kind) { "photo" -> "Photo"; "text" -> "Text"; else -> "Sticker" }, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(end = 4.dp))
            if (kind == "photo") FilterChip(m.crop, { m.crop = !m.crop; m.touch() }, label = { Text(if (m.crop) "Done" else "Crop") }, leadingIcon = { Icon(Icons.Default.Crop, null, Modifier.size(16.dp)) })
            OutlinedButton(onClick = { l.put("x", 540); m.commit() }) { Text("Centre") }
            OutlinedButton(onClick = { m.move(l, 1) }) { Text("Forward") }
            OutlinedButton(onClick = { m.move(l, -1) }) { Text("Back") }
            OutlinedButton(onClick = { m.add(JSONObject(l.toString()).put("id", CoverDraw.uid()).put("x", l.optDouble("x") + 40).put("y", l.optDouble("y") + 40)) }) { Text("Duplicate") }
            OutlinedButton(onClick = { m.sel = null; m.crop = false }) { Text("Close") }
        }
        when (kind) {
            "photo" -> {
                if (m.crop) Sl("Crop zoom", l.getJSONObject("crop").optDouble("z", 1.0), 1f, 6f, { l.getJSONObject("crop").put("z", it); m.touch() }) { m.commit() }
                Sl("Width", l.optDouble("w"), 60f, 1600f, live("w")) { m.commit() }
                Sl("Height", l.optDouble("h"), 60f, 2200f, live("h")) { m.commit() }
                Chips(listOf("rect" to "Square corners", "round" to "Rounded", "circle" to "Circle"), l.optString("shape", "round")) {
                    l.put("shape", it); if (it == "circle" && abs(l.optDouble("w") - l.optDouble("h")) > 4) l.put("h", l.optDouble("w")); m.commit()
                }
                Sl("Border", l.optDouble("border", 0.0), 0f, 40f, { l.put("border", Math.round(it)); m.touch() }) { m.commit() }
                Swatches(l.optString("bcolor")) { l.put("bcolor", it); m.commit() }
                Sl("Rotate", l.optDouble("rot", 0.0), -180f, 180f, { l.put("rot", Math.round(it)); m.touch() }) { m.commit() }
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    val sh = l.optInt("shadow", 0) != 0 || l.optBoolean("shadow")
                    FilterChip(sh, { l.put("shadow", !sh); m.commit() }, label = { Text("Shadow") })
                    FilterChip(l.optBoolean("flip"), { l.put("flip", !l.optBoolean("flip")); m.commit() }, label = { Text("Mirror") })
                    FilterChip(false, { l.put("h", l.optDouble("w")); m.commit() }, label = { Text("Make square") })
                    FilterChip(false, {
                        val b = images[l.optString("src")]; if (b != null) l.put("h", Math.round(l.optDouble("w") * b.height / b.width).toDouble())
                        l.put("crop", JSONObject().put("z", 1).put("ox", 0.5).put("oy", 0.5)); m.commit()
                    }, label = { Text("Original shape") })
                }
            }
            "text" -> {
                OutlinedTextField(words, { words = it; l.put("text", it); m.touch() }, Modifier.fillMaxWidth(), label = { Text("Words") }, maxLines = 3)
                LaunchedEffect(words) { kotlinx.coroutines.delay(600); m.commit() }
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    CoverDraw.LOOKS.forEach { (k, o) -> LookTile(o) { CoverDraw.applyLook(l, k); m.commit() } }
                }
                Chips(FONTS.map { it to it }, l.optString("font")) { l.put("font", it); m.commit() }
                Sl("Size", l.optDouble("size", 120.0), 24f, 400f, { l.put("size", Math.round(it)); m.touch() }) { m.commit() }
                Text("Text colour", fontSize = 12.sp); Swatches(l.optString("color")) { l.put("color", it); m.commit() }
                Text("Outline colour", fontSize = 12.sp); Swatches(l.optString("stroke")) { l.put("stroke", it); m.commit() }
                Sl("Outline", l.optDouble("strokeW", 0.0), 0f, 40f, { l.put("strokeW", Math.round(it)); m.touch() }) { m.commit() }
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    val hasBox = l.optString("box").isNotEmpty(); val sh = l.optInt("shadow", 0) != 0 || l.optBoolean("shadow")
                    FilterChip(hasBox, { l.put("box", if (hasBox) "" else "#000000"); m.commit() }, label = { Text("Box") })
                    FilterChip(sh, { l.put("shadow", !sh); m.commit() }, label = { Text("Shadow") })
                    FilterChip(l.optBoolean("upper"), { l.put("upper", !l.optBoolean("upper")); m.commit() }, label = { Text("CAPITALS") })
                }
                if (l.optString("box").isNotEmpty()) { Text("Box colour", fontSize = 12.sp); Swatches(l.optString("box")) { l.put("box", it); m.commit() } }
                Sl("Rotate", l.optDouble("rot", 0.0), -180f, 180f, { l.put("rot", Math.round(it)); m.touch() }) { m.commit() }
            }
            else -> {
                Sl("Size", l.optDouble("size", 220.0), 40f, 1000f, { l.put("size", Math.round(it)); m.touch() }) { m.commit() }
                Sl("Rotate", l.optDouble("rot", 0.0), -180f, 180f, { l.put("rot", Math.round(it)); m.touch() }) { m.commit() }
                if (CoverDraw.SHAPES.any { it.first == l.optString("t") }) {
                    Swatches(l.optString("color")) { l.put("color", it); m.commit() }
                    Sl("Line", l.optDouble("lw", 22.0), 6f, 60f, { l.put("lw", Math.round(it)); m.touch() }) { m.commit() }
                }
            }
        }
    }
}
