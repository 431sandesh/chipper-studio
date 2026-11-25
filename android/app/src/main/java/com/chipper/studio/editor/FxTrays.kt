package com.chipper.studio.editor

import android.content.Context
import android.media.MediaPlayer
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chipper.studio.AppModel
import com.chipper.studio.net.objects
import com.chipper.studio.net.strings
import com.chipper.studio.ui.LocalToast
import com.chipper.studio.ui.Pink
import com.chipper.studio.ui.PinkDeep
import com.chipper.studio.ui.Violet
import com.chipper.studio.ui.enc
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max

/** Stickers and meme combos, exported from the laptop's editor so both draw the same thing. */
object Effects {
    private var data: JSONObject? = null
    fun load(ctx: Context): JSONObject = data ?: JSONObject(ctx.assets.open("effects.json").bufferedReader().readText()).also { data = it }
    val FX = linkedMapOf("zoom" to Triple("🔍", "Zoom punch", 0.6), "shake" to Triple("📳", "Shake", 0.8), "flash" to Triple("⚡", "Flash", 0.35),
        "bw" to Triple("⚫", "Black & white", 2.0), "red" to Triple("🩸", "Red drama", 2.0), "glitch" to Triple("👾", "Glitch", 0.5),
        "fry" to Triple("🍟", "Deep fry", 1.0), "blue" to Triple("🌧", "Sad blue", 2.5), "dark" to Triple("🌑", "Dark corners", 2.5))
}

/** Plays a short sound from the laptop (with the login cookie). */
class Sounds(private val ctx: Context, private val app: AppModel) {
    private var mp: MediaPlayer? = null
    fun play(path: String, fromSec: Double = 0.0, maxSec: Double = 0.0) {
        stop()
        val p = MediaPlayer()
        runCatching {
            p.setDataSource(ctx, Uri.parse(app.api.url(path)), mapOf("Cookie" to app.api.cookieHeader()))
            p.setOnPreparedListener { if (fromSec > 0) it.seekTo((fromSec * 1000).toInt()); it.start() }
            p.prepareAsync()
        }
        mp = p
        if (maxSec > 0) android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({ if (mp === p) stop() }, (maxSec * 1000).toLong())
    }
    fun stop() { mp?.let { runCatching { it.stop() }; it.release() }; mp = null }
}

fun sfxPath(name: String) = if (name.startsWith("file:")) "/sounds/${enc(name.removePrefix("file:"))}" else "/sfx/${enc(name)}.wav"

/** A square tile: first tap previews (armed, pink edge), a second tap on the same tile applies it. */
@Composable
fun Tile(top: String, label: String, armed: Boolean, on: Boolean = false, onClick: () -> Unit) {
    Box(Modifier.size(76.dp).clip(RoundedCornerShape(12.dp)).background(if (on) Pink.copy(alpha = 0.12f) else MaterialTheme.colorScheme.surfaceVariant)
        .border(2.dp, if (armed || on) Pink else Color.Transparent, RoundedCornerShape(12.dp)).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(top, fontSize = 22.sp)
            Text(label, fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center, maxLines = 2, lineHeight = 12.sp, modifier = Modifier.padding(horizontal = 3.dp))
        }
        if (armed) Text("Tap to add", Modifier.align(Alignment.BottomCenter).fillMaxWidth().background(Pink).padding(vertical = 1.dp), color = Color.White, fontSize = 9.sp, textAlign = TextAlign.Center, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun TileRow(content: @Composable () -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) { content() }
}

/** Audio & FX: sounds and music, meme combos at the playhead, stickers, visual effects, loop and rise. */
@Composable
fun FxTray(app: AppModel, m: EdModel, t: Double, playPreview: (Double) -> Unit) {
    val ctx = LocalContext.current
    val toast = LocalToast.current
    val eff = remember { Effects.load(ctx) }
    val snd = remember { Sounds(ctx, app) }
    DisposableEffect(Unit) { onDispose { snd.stop() } }
    var tab by remember { mutableStateOf("snd") }
    var armed by remember { mutableStateOf<String?>(null) }
    var before by remember { mutableStateOf<String?>(null) }       // scene before a preview, to put back if not kept
    var sfx by remember { mutableStateOf<List<Pair<String, String>>>(emptyList()) }
    var mine by remember { mutableStateOf<List<String>>(emptyList()) }
    var music by remember { mutableStateOf<List<String>>(emptyList()) }
    var fresh by remember { mutableStateOf<List<String>>(emptyList()) }   // just uploaded: shown first
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    suspend fun reloadSounds() {
        runCatching { app.load("/api/config") }.getOrNull()?.let { c -> mine = c.optJSONArray("sounds")?.strings() ?: mine }
        music = runCatching { app.api.get("/api/music").optJSONArray("files")?.strings() ?: emptyList() }.getOrDefault(music)
    }
    val upload = com.chipper.studio.net.rememberUploader(app, "audio/*", kindFor = { c, u -> if (com.chipper.studio.net.audioSeconds(c, u) > 30) "audio" else "fx" }) { done ->
        fresh = done.map { it.optString("file") } + fresh
        scope.launch { reloadSounds() }
    }
    LaunchedEffect(Unit) {
        (app.cached("/api/config") ?: runCatching { app.load("/api/config") }.getOrNull())?.let { c ->
            sfx = c.optJSONObject("sfx")?.let { o -> o.keys().asSequence().map { it to o.optString(it) }.toList() } ?: emptyList()
            mine = c.optJSONArray("sounds")?.strings() ?: emptyList()
        }
        music = runCatching { app.api.get("/api/music").optJSONArray("files")?.strings() ?: emptyList() }.getOrDefault(emptyList())
    }
    fun undoPreview() { before?.let { m.restoreSnap(it) }; before = null }
    fun arm(key: String, preview: () -> Unit, apply: () -> Unit) {
        if (armed == key) {                       // second tap: keep it
            if (before != null) { before = null; m.commit(); toast.show("Added at the playhead") } else { apply(); m.commit(); toast.show("Added at the playhead") }
            armed = null; snd.stop(); return
        }
        undoPreview(); armed = key; preview()
    }
    fun sfxArr() = m.scene.optJSONArray("sfx") ?: JSONArray().also { m.scene.put("sfx", it) }
    fun fxArr() = m.scene.optJSONArray("fx") ?: JSONArray().also { m.scene.put("fx", it) }
    val at = { max(0.0, t - m.start) }
    fun addFx(type: String, start: Double, dur: Double, amt: Double = 0.7, color: String = "#ffffff") =
        fxArr().put(JSONObject().put("id", m.newId()).put("type", type).put("start", Math.round(start * 100) / 100.0).put("dur", dur).put("amt", amt).put("color", color))
    fun addSfx(sound: String, start: Double) = sfxArr().put(JSONObject().put("id", m.newId()).put("sound", sound).put("start", Math.round(start * 100) / 100.0).put("vol", 0.9))
    fun addSticker(k: String, t0: Double, dur: Double? = null, at2: JSONArray? = null): JSONObject {
        val s = eff.getJSONObject("stickers").getJSONObject(k)
        val l = JSONObject().put("kind", "third").put("id", m.newId()).put("name", "Sticker").put("title", s.optString("label")).put("template", "sticker")
            .put("platform", "").put("sub", "").put("accent", "#ffffff").put("prims", JSONArray(s.getJSONArray("prims").toString()))
            .put("x", at2?.optDouble(0) ?: s.optDouble("x")).put("y", at2?.optDouble(1) ?: s.optDouble("y")).put("scale", 1).put("start", Math.round(t0 * 10) / 10.0)
            .put("dur", dur ?: s.optDouble("dur")).put("anim", "fade")
        m.layers().put(l); return l
    }
    fun addCombo(c: JSONObject) {
        val t0 = at()
        c.optJSONArray("fx")?.let { a -> for (i in 0 until a.length()) a.getJSONArray(i).let { f -> addFx(f.getString(0), t0 + f.getDouble(1), f.getDouble(2), f.optDouble(3, 0.7), f.optString(4, "#ffffff")) } }
        c.optJSONArray("sfx")?.let { a -> for (i in 0 until a.length()) a.getJSONArray(i).let { f -> addSfx(f.getString(0), t0 + f.optDouble(1, 0.0)) } }
        c.optJSONArray("stk")?.let { a -> for (i in 0 until a.length()) a.getJSONArray(i).let { f -> addSticker(f.getString(0), t0, if (f.length() > 1 && !f.isNull(1)) f.getDouble(1) else null, f.optJSONArray(2)) } }
        c.optJSONArray("text")?.let { tx ->
            m.layers().put(JSONObject().put("kind", "text").put("id", m.newId()).put("name", "Effect text").put("text", tx.getString(0)).put("x", 540).put("y", 820).put("size", 150)
                .put("color", tx.getString(1)).put("box", false).put("bcolor", "#000000").put("opacity", 1).put("font", "Impact").put("start", Math.round(t0 * 10) / 10.0).put("end", Math.round((t0 + tx.getDouble(2)) * 10) / 10.0))
        }
    }
    // a preview that changes the clip: apply it now, play it, and remember how to undo it
    fun visualPreview(apply: () -> Unit) { before = m.snapshot(); apply(); m.rev++; playPreview(1.8) }

    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface).padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("snd" to "Sounds", "combo" to "Playhead", "stk" to "Stickers", "vfx" to "Visual FX", "loop" to "Loop & rise").forEach { (k, l) ->
                FilterChip(tab == k, { undoPreview(); armed = null; snd.stop(); tab = k }, label = { Text(l) })
            }
        }
        when (tab) {
            "snd" -> {
                val cur = m.scene.optJSONObject("music")?.optString("file") ?: ""
                TileRow {
                    Tile("⬆️", "Upload", false, true) { upload() }
                    music.sortedBy { if (it in fresh) 0 else 1 }.forEach { f ->
                        val on = cur == f
                        Tile(if (on) "✅" else "🎵", f.removePrefix("music_").substringBeforeLast('.').take(16), armed == "mu:$f", on) {
                            arm("mu:$f", { if (!on) snd.play("/assets/${enc(f)}", 0.0, 6.0) else toast.show("Tap again to remove this music") }) {
                                val mu = m.scene.optJSONObject("music") ?: JSONObject().put("vol", 0.15).put("peak", 0.45).put("rise", 4).put("duck", true).put("offset", 0).also { m.scene.put("music", it) }
                                mu.put("file", if (on) "" else f)
                            }
                        }
                    }
                    mine.sortedBy { if (it in fresh) 0 else 1 }.forEach { f -> Tile("🎙", f.substringBeforeLast('.').take(16), armed == "my:$f") { arm("my:$f", { snd.play(sfxPath("file:$f")) }) { addSfx("file:$f", at()) } } }
                    sfx.forEach { (k, l) -> Tile("🔊", l, armed == "sk:$k") { arm("sk:$k", { snd.play(sfxPath(k)) }) { addSfx(k, at()) } } }
                }
                m.scene.optJSONObject("music")?.takeIf { it.optString("file").isNotEmpty() }?.let { mu ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Music volume", Modifier.width(100.dp), fontSize = 13.sp)
                        Slider(mu.optDouble("vol", 0.15).toFloat(), { mu.put("vol", it.toDouble()); m.rev++ }, Modifier.weight(1f), valueRange = 0f..1f, onValueChangeFinished = { m.commit() })
                    }
                }
                Text("Long sounds (over 30 s) go to your music, short ones to sound effects. They're kept on your PC in uploads\\audio and uploads\\fx.", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            "combo" -> TileRow {
                eff.getJSONArray("combos").objects().forEachIndexed { i, c ->
                    Tile(c.optString("emoji"), c.optString("label"), armed == "co:$i") { arm("co:$i", { visualPreview { addCombo(c) } }) { addCombo(c) } }
                }
            }
            "stk" -> TileRow {
                val st = eff.getJSONObject("stickers")
                st.keys().forEach { k -> val s = st.getJSONObject(k); Tile(s.optString("emoji"), s.optString("label"), armed == "st:$k") { arm("st:$k", { visualPreview { addSticker(k, at()) } }) { addSticker(k, at()) } } }
            }
            "vfx" -> TileRow {
                Effects.FX.forEach { (k, v) -> Tile(v.first, v.second, armed == "fx:$k") { arm("fx:$k", { visualPreview { addFx(k, at(), v.third) } }) { addFx(k, at(), v.third) } } }
            }
            "loop" -> Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Loop the ending smoothly", Modifier.weight(1f)); Switch(m.scene.optBoolean("loop"), { m.scene.put("loop", it); m.commit() })
                }
                val mu = m.scene.optJSONObject("music")
                if (mu != null && mu.optString("file").isNotEmpty()) Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Rise at end ${mu.optDouble("rise", 4.0).toInt()} s", Modifier.width(120.dp), fontSize = 13.sp)
                    Slider(mu.optDouble("rise", 4.0).toFloat(), { mu.put("rise", Math.round(it).toDouble()); m.rev++ }, Modifier.weight(1f), valueRange = 0f..10f, onValueChangeFinished = { m.commit() })
                } else Text("Rise at the end needs background music. Pick it under Sounds.", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        val n = (m.scene.optJSONArray("fx")?.length() ?: 0) + (m.scene.optJSONArray("sfx")?.length() ?: 0)
        if (n > 0) Text("$n sounds and effects on this clip. They play from where the playhead was.", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    DisposableEffect(tab) { onDispose { } }
    DisposableEffect(Unit) { onDispose { undoPreview() } }
}

/** Lower thirds: template and platform, channel name (filled from your connected accounts), font, animation, add at the playhead. */
@Composable
fun ThirdsTray(app: AppModel, m: EdModel, t: Double) {
    val toast = LocalToast.current
    var tab by remember { mutableStateOf("tpl") }
    var tpl by remember { mutableStateOf("darkpill") }
    var plat by remember { mutableStateOf("youtube") }
    var name by remember { mutableStateOf("") }
    var sub by remember { mutableStateOf("") }
    var font by remember { mutableStateOf("") }
    var color by remember { mutableStateOf("") }
    var size by remember { mutableStateOf(1.0) }
    var anim by remember { mutableStateOf("slide") }
    var dur by remember { mutableStateOf(4.0) }
    var accts by remember { mutableStateOf<Map<String, List<String>>>(emptyMap()) }
    LaunchedEffect(Unit) {
        runCatching { app.api.get("/api/accounts").optJSONObject("accounts") }.getOrNull()?.let { a ->
            accts = mapOf(
                "youtube" to (a.optJSONObject("youtube")?.optJSONArray("channels")?.objects()?.map { it.optString("title") } ?: emptyList()),
                "instagram" to (a.optJSONObject("instagram")?.optJSONArray("accounts")?.objects()?.map { "@" + it.optString("username") } ?: emptyList()),
                "facebook" to (a.optJSONObject("facebook")?.optJSONArray("pages")?.objects()?.map { it.optString("name") } ?: emptyList()),
                "tiktok" to (a.optJSONObject("tiktok")?.optJSONArray("accounts")?.objects()?.map { "@" + it.optString("username").ifEmpty { it.optString("name") } } ?: emptyList()),
            )
            accts[plat]?.firstOrNull()?.let { if (name.isEmpty()) name = it }
        }
    }
    Column(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface).padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("tpl" to "Template", "chan" to "Channel", "font" to "Font", "anim" to "Animation", "add" to "Add to clip").forEach { (k, l) -> FilterChip(tab == k, { tab = k }, label = { Text(l) }) }
        }
        when (tab) {
            "tpl" -> {
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Thirds.PLATFORMS.forEach { (k, p) -> FilterChip(plat == k, { plat = k; accts[k]?.let { l -> if (l.size == 1 || (l.isNotEmpty() && name !in l)) name = l.first() } }, label = { Text(p.label) }) }
                }
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Thirds.TEMPLATES.forEach { (k, l) -> Tile(if (tpl == k) "✅" else "🏷", l, false, tpl == k) { tpl = k } }
                }
            }
            "chan" -> {
                val list = accts[plat] ?: emptyList()
                OutlinedTextField(name, { name = it }, Modifier.fillMaxWidth(), singleLine = true, label = { Text("Channel name or @handle") })
                if (list.isNotEmpty()) Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    list.forEach { n -> FilterChip(name == n, { name = n }, label = { Text(n) }) }
                } else Text("No ${Thirds.PLATFORMS[plat]?.label} account connected. Type the name.", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                OutlinedTextField(sub, { sub = it }, Modifier.fillMaxWidth(), singleLine = true, label = { Text("Second line (optional)") })
            }
            "font" -> {
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("" to "Template", "Arial Black" to "Arial Black", "Impact" to "Impact", "Montserrat" to "Montserrat", "Bebas Neue" to "Bebas", "Poppins" to "Poppins").forEach { (k, l) -> FilterChip(font == k, { font = k }, label = { Text(l) }) }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    listOf("", "#ffffff", "#111111", "#ffe11a", "#ff3d6e", "#2ee6c8").forEach { c ->
                        Box(Modifier.size(30.dp).clip(RoundedCornerShape(15.dp)).background(if (c.isEmpty()) Color.LightGray else Color(android.graphics.Color.parseColor(c)))
                            .border(2.dp, if (color == c) Pink else Color.Transparent, RoundedCornerShape(15.dp)).clickable { color = c }, contentAlignment = Alignment.Center) { if (c.isEmpty()) Text("↺", fontSize = 13.sp) }
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) { Text("Size ${(size * 100).toInt()}%", Modifier.width(90.dp), fontSize = 13.sp); Slider(size.toFloat(), { size = it.toDouble() }, Modifier.weight(1f), valueRange = 0.6f..1.6f) }
            }
            "anim" -> Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Thirds.ANIMS.forEach { (k, l) -> Tile("✨", l, false, anim == k) { anim = k } }
            }
            "add" -> Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Stays ${"%.1f".format(dur)} s", Modifier.width(96.dp), fontSize = 13.sp)
                Slider(dur.toFloat(), { dur = Math.round(it * 2) / 2.0 }, Modifier.weight(1f), valueRange = 1f..max(2f, m.len().toFloat()))
                Spacer(Modifier.width(8.dp))
                Button(onClick = {
                    if (name.isBlank()) { toast.show("Type your channel name first"); tab = "chan"; return@Button }
                    val prims = Thirds.build(tpl, name.trim(), sub.trim(), plat, font, color, size)
                    val l = JSONObject().put("kind", "third").put("id", m.newId()).put("name", (Thirds.TEMPLATES.toMap()[tpl] ?: "Lower third") + " (" + Thirds.PLATFORMS[plat]?.label + ")")
                        .put("template", tpl).put("platform", plat).put("title", name.trim()).put("sub", sub.trim()).put("accent", Thirds.PLATFORMS[plat]?.color ?: "#ff0000")
                        .put("prims", prims).put("scale", 1).put("start", Math.round(max(0.0, t - m.start) * 10) / 10.0).put("dur", dur).put("anim", anim)
                        .put("topts", JSONObject().put("font", font).put("color", color).put("size", size))
                    val b = m.primsBox(l); l.put("x", Math.round((1080 - b[2]) / 2.0)).put("y", 1500)
                    m.layers().put(l); m.sel = l.optString("id"); m.commit(); toast.show("Lower third added at the playhead. Drag it to place it")
                }) { Text("＋ Add") }
            }
        }
    }
}
