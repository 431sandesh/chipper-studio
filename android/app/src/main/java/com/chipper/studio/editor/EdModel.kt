package com.chipper.studio.editor

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max
import kotlin.math.min

const val W = 1080f
const val H = 1920f

data class Word(val a: Double, val b: Double, val t: String)

/**
 * The clip being edited. The scene stays as JSON so every field the laptop knows about survives a round trip,
 * even ones this app does not edit yet. Layer times are seconds from the clip start; words, cuts and
 * crop keyframes use the source video's own time, like on the laptop.
 */
class EdModel(val clip: JSONObject, val styles: JSONObject) {
    val name: String = clip.optString("name")
    var scene: JSONObject = clip.optJSONObject("scene") ?: JSONObject().put("layers", JSONArray())
    val words: MutableList<Word> = clip.optJSONArray("words")?.let { a ->
        (0 until a.length()).mapNotNull { i -> a.optJSONArray(i)?.let { w -> Word(w.optDouble(0), w.optDouble(1), w.optString(2)) } }.toMutableList()
    } ?: mutableListOf()
    var start by mutableStateOf(clip.optDouble("trim_start", 0.0))
    var end by mutableStateOf(clip.optDouble("trim_end", 0.0))
    var dur by mutableStateOf(0.0)                     // length of the whole source video
    val cuts: MutableList<Pair<Double, Double>> = clip.optJSONArray("cuts")?.let { a ->
        (0 until a.length()).mapNotNull { i -> a.optJSONArray(i)?.let { c -> c.optDouble(0) to c.optDouble(1) } }.toMutableList()
    } ?: mutableListOf()
    var cutA by mutableStateOf<Double?>(null)
    var hook by mutableStateOf(clip.optString("hook"))
    var sel by mutableStateOf<String?>(null)
    /** Bumped on every change so the preview and panels redraw. */
    var rev by mutableIntStateOf(0)
    var dirty by mutableStateOf(false)
    var srcW = 1920; var srcH = 1080
    val imgAr = HashMap<String, Float>()   // width / height of each image, once loaded

    private val hist = mutableListOf<String>()
    private var hi = -1

    init {
        if (end <= start) end = 0.0
        if (!scene.has("bg")) scene.put("bg", JSONObject().put("type", "blur").put("color", "#111318").put("dim", 0.25).put("blur", 14))
        commit(false)
    }

    fun layers(): JSONArray = scene.optJSONArray("layers") ?: JSONArray().also { scene.put("layers", it) }
    fun list(): List<JSONObject> = layers().let { a -> (0 until a.length()).map { a.getJSONObject(it) } }
    fun byId(id: String?): JSONObject? = list().firstOrNull { it.optString("id") == id }
    fun selected(): JSONObject? = byId(sel)
    fun of(kind: String): JSONObject? = list().firstOrNull { it.optString("kind") == kind }

    /** Draw order: video windows and images first, then everything else, each keeping its list order. */
    fun order(): List<JSONObject> = list().let { l -> l.filter { it.optString("kind") in BACK } + l.filter { it.optString("kind") !in BACK } }

    fun touch() { rev++; dirty = true }

    // ---------------------------------------------------------------- undo
    private fun snap() = JSONObject().put("scene", JSONObject(scene.toString())).put("start", start).put("end", end)
        .put("cuts", JSONArray(cuts.map { JSONArray(listOf(it.first, it.second)) })).put("hook", hook).toString()

    fun commit(mark: Boolean = true) {
        val s = snap(); if (hi >= 0 && hist[hi] == s) return
        while (hist.size > hi + 1) hist.removeAt(hist.size - 1)
        hist += s; if (hist.size > 80) hist.removeAt(0); hi = hist.size - 1
        if (mark) dirty = true; rev++
    }
    /** A copy of the current state, for a preview that may be put back without touching undo history. */
    fun snapshot(): String = snap()
    fun restoreSnap(s: String) {
        val o = JSONObject(s)
        scene = o.getJSONObject("scene"); start = o.getDouble("start"); end = o.getDouble("end"); hook = o.optString("hook")
        cuts.clear(); o.getJSONArray("cuts").let { a -> for (k in 0 until a.length()) a.getJSONArray(k).let { c -> cuts += c.getDouble(0) to c.getDouble(1) } }
        if (selected() == null) sel = null
        rev++
    }
    val canUndo get() = hi > 0
    val canRedo get() = hi < hist.size - 1
    fun undo() { if (canUndo) restore(--hi) }
    fun redo() { if (canRedo) restore(++hi) }
    private fun restore(i: Int) {
        val o = JSONObject(hist[i])
        scene = o.getJSONObject("scene"); start = o.getDouble("start"); end = o.getDouble("end"); hook = o.optString("hook")
        cuts.clear(); o.getJSONArray("cuts").let { a -> for (k in 0 until a.length()) a.getJSONArray(k).let { c -> cuts += c.getDouble(0) to c.getDouble(1) } }
        if (selected() == null) sel = null
        dirty = true; rev++
    }

    // ---------------------------------------------------------------- time
    fun len() = max(0.5, end - start)
    fun visible(l: JSONObject, tRel: Double): Boolean {
        if (l.optBoolean("hidden")) return false
        return when (l.optString("kind")) {
            "hook" -> tRel < l.optDouble("secs", 3.5)
            "text", "image" -> { val s = l.optDouble("start", 0.0); val e = l.optDouble("end", 0.0); tRel >= s && (e <= 0 || tRel <= e) }
            "third" -> {
                val s = l.optDouble("start", 0.0); val d = l.optDouble("dur", 0.0)
                if (l.optBoolean("from_end")) tRel >= len() - d else tRel >= s && (d <= 0 || tRel <= s + d)
            }
            else -> true
        }
    }
    fun inCut(t: Double): Pair<Double, Double>? = cuts.firstOrNull { t >= it.first && t < it.second }

    // ---------------------------------------------------------------- crop with keyframes (position and size)
    private fun smooth(u: Double) = u * u * (3 - 2 * u)
    fun cropAt(l: JSONObject, t: Double): DoubleArray {
        val c = l.optJSONArray("crop")?.let { a -> DoubleArray(4) { a.optDouble(it) } } ?: doubleArrayOf(0.0, 0.0, 1.0, 1.0)
        val k = l.optJSONArray("keys") ?: return c
        if (k.length() == 0) return c
        val ks = (0 until k.length()).map { k.getJSONArray(it) }.sortedBy { it.getDouble(0) }
        fun kv(q: JSONArray) = doubleArrayOf(q.getDouble(1), q.getDouble(2), if (q.length() == 5) q.getDouble(3) else c[2], if (q.length() == 5) q.getDouble(4) else c[3])
        val v: DoubleArray = when {
            t <= ks.first().getDouble(0) -> kv(ks.first())
            t >= ks.last().getDouble(0) -> kv(ks.last())
            else -> {
                var i = 0; while (i < ks.size - 2 && t >= ks[i + 1].getDouble(0)) i++
                val a = kv(ks[i]); val b = kv(ks[i + 1])
                if (l.optString("kmode") == "cut") a else {
                    val u = smooth(((t - ks[i].getDouble(0)) / max(0.01, ks[i + 1].getDouble(0) - ks[i].getDouble(0))).coerceIn(0.0, 1.0))
                    DoubleArray(4) { j -> a[j] + (b[j] - a[j]) * u }
                }
            }
        }
        val w = v[2].coerceIn(0.02, 1.0); val h = v[3].coerceIn(0.02, 1.0)
        return doubleArrayOf(v[0].coerceIn(0.0, 1 - w), v[1].coerceIn(0.0, 1 - h), w, h)
    }

    /** Keep the crop the same shape as its window. */
    fun fitCrop(l: JSONObject) {
        val c = l.optJSONArray("crop")?.let { a -> DoubleArray(4) { a.optDouble(it) } } ?: doubleArrayOf(0.0, 0.0, 1.0, 1.0)
        val ar = l.optDouble("w", W.toDouble()) / max(1.0, l.optDouble("h", H.toDouble()))
        var w = c[2] * srcW; var h = w / ar
        if (h > srcH) { h = srcH.toDouble(); w = h * ar }
        if (w > srcW) { w = srcW.toDouble(); h = w / ar }
        val nw = w / srcW; val nh = h / srcH; val mx = c[0] + c[2] / 2; val my = c[1] + c[3] / 2
        l.put("crop", JSONArray(listOf((mx - nw / 2).coerceIn(0.0, 1 - nw), (my - nh / 2).coerceIn(0.0, 1 - nh), nw, nh)))
    }

    fun setKey(l: JSONObject, t: Double) {
        val keys = l.optJSONArray("keys") ?: JSONArray().also { l.put("keys", it) }
        val c = l.getJSONArray("crop")
        val k = JSONArray(listOf(Math.round(t * 100) / 100.0, c.getDouble(0), c.getDouble(1), c.getDouble(2), c.getDouble(3)))
        for (i in 0 until keys.length()) if (kotlin.math.abs(keys.getJSONArray(i).getDouble(0) - t) < 0.15) { keys.put(i, k); return }
        keys.put(k)
    }

    // ---------------------------------------------------------------- captions
    fun capStyle(): JSONObject {
        val c = of("captions")
        val st = JSONObject((styles.optJSONObject(c?.optString("style") ?: "tiktok") ?: styles.optJSONObject("tiktok") ?: JSONObject()).toString())
        c?.let {
            if (it.optString("color").isNotEmpty()) st.put("color", it.optString("color"))
            if (it.optString("hi").isNotEmpty()) st.put("hi", it.optString("hi"))
            if (it.optInt("words") > 0) st.put("words", it.optInt("words"))
            if (it.optString("font").isNotEmpty()) st.put("font", it.optString("font"))
            if (it.optString("anim") == "onebyone") st.put("words", 1)
        }
        return st
    }
    fun groups(): List<List<Int>> {
        val n = capStyle().optInt("words", 3).coerceAtLeast(1)
        val out = mutableListOf<List<Int>>(); var cur = mutableListOf<Int>()
        words.forEachIndexed { i, w ->
            val prev = cur.lastOrNull()?.let { words[it] }
            if (cur.isNotEmpty() && (cur.size >= n || w.a - prev!!.b > 0.6 || Regex("[.!?]$").containsMatchIn(prev.t))) { out += cur; cur = mutableListOf() }
            cur += i
        }
        if (cur.isNotEmpty()) out += cur
        return out
    }
    fun groupAt(t: Double): List<Int>? = groups().firstOrNull { g -> t >= words[g.first()].a && t <= words[g.last()].b + 0.15 }

    // ---------------------------------------------------------------- layout
    /** Rectangle of a layer in canvas pixels: left, top, width, height. */
    fun bounds(l: JSONObject, imgAr: Float = 1f): FloatArray = when (l.optString("kind")) {
        "video" -> floatArrayOf(l.optDouble("x").toFloat(), l.optDouble("y").toFloat(), l.optDouble("w", 1080.0).toFloat(), l.optDouble("h", 1920.0).toFloat())
        "image" -> { val w = l.optDouble("w", 300.0).toFloat(); val c = l.optJSONArray("crop"); val cw = c?.optDouble(2, 1.0)?.toFloat() ?: 1f; val ch = c?.optDouble(3, 1.0)?.toFloat() ?: 1f
            val h = w * ch / cw / imgAr; floatArrayOf(l.optDouble("x").toFloat() - w / 2, l.optDouble("y").toFloat() - h / 2, w, h) }
        "text" -> { val s = l.optDouble("size", 70.0).toFloat(); val lines = l.optString("text").split("\n"); val w = min(1000f, lines.maxOf { it.length } * s * 0.6f + s * 0.6f); val h = lines.size * s * 1.25f
            floatArrayOf(l.optDouble("x").toFloat() - w / 2, l.optDouble("y").toFloat() - h / 2, w, h) }
        "captions" -> { val s = (capStyle().optDouble("size", 90.0) * l.optDouble("scale", 1.0)).toFloat(); floatArrayOf(60f, l.optDouble("y").toFloat() - s * 1.2f, 960f, s * 2.4f) }
        "hook" -> { val s = (84 * l.optDouble("scale", 1.0)).toFloat(); floatArrayOf(90f, l.optDouble("y").toFloat() - s * 1.2f, 900f, s * 2.4f) }
        "third" -> { val b = primsBox(l); val s = l.optDouble("scale", 1.0).toFloat()
            floatArrayOf(l.optDouble("x").toFloat() + b[0] * s, l.optDouble("y").toFloat() + b[1] * s, b[2] * s, b[3] * s) }
        else -> floatArrayOf(0f, 0f, 100f, 100f)
    }

    fun primsBox(l: JSONObject): FloatArray {
        val ps = l.optJSONArray("prims") ?: return floatArrayOf(0f, 0f, 300f, 100f)
        var x0 = 1e9f; var y0 = 1e9f; var x1 = -1e9f; var y1 = -1e9f
        for (i in 0 until ps.length()) {
            val p = ps.getJSONObject(i)
            when (p.optString("t")) {
                "rr" -> { val x = p.optDouble("x").toFloat(); val y = p.optDouble("y").toFloat(); x0 = min(x0, x); y0 = min(y0, y); x1 = max(x1, x + p.optDouble("w").toFloat()); y1 = max(y1, y + p.optDouble("h").toFloat()) }
                "poly" -> p.optJSONArray("pts")?.let { a -> for (k in 0 until a.length()) a.getJSONArray(k).let { q -> x0 = min(x0, q.getDouble(0).toFloat()); x1 = max(x1, q.getDouble(0).toFloat()); y0 = min(y0, q.getDouble(1).toFloat()); y1 = max(y1, q.getDouble(1).toFloat()) } }
                else -> { val x = p.optDouble("x").toFloat(); val y = p.optDouble("y").toFloat(); val s = p.optDouble("size", 30.0).toFloat()
                    x0 = min(x0, x); x1 = max(x1, x + p.optString("text").length * s * 0.6f); y0 = min(y0, y - s / 2); y1 = max(y1, y + s / 2) }
            }
        }
        return if (x0 > x1) floatArrayOf(0f, 0f, 300f, 100f) else floatArrayOf(x0, y0, x1 - x0, y1 - y0)
    }

    fun newId() = (1..7).map { "abcdefghijklmnopqrstuvwxyz0123456789".random() }.joinToString("")

    /** What is sent back to the laptop to save and re-render. */
    fun payload(): JSONObject = JSONObject().put("scene", scene).put("trim_start", start).put("trim_end", end).put("hook", hook)
        .put("cuts", JSONArray(cuts.sortedBy { it.first }.map { JSONArray(listOf(it.first, it.second)) }))

    companion object { val BACK = setOf("video", "image") }
}
