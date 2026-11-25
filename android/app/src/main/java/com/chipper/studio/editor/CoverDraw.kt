package com.chipper.studio.editor

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max
import kotlin.math.min

/**
 * Draws a thumbnail design (the same "v2" design the laptop's thumbnail editor saves):
 * a background picture with colour and blur, then photos, text and stickers, each moved, sized and rotated.
 * Coordinates are on a 1080x1920 cover.
 */
object CoverDraw {
    const val CW = 1080f
    const val CH = 1920f
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeJoin = Paint.Join.ROUND; strokeCap = Paint.Cap.ROUND }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG)
    private val blurCache = HashMap<String, Bitmap>()

    val SHAPES = listOf("arrow" to "Arrow", "ring" to "Circle", "box" to "Box", "x" to "Cross")
    val EMOJI = listOf("😱", "🤯", "😂", "💀", "🔥", "😳", "🤑", "👀", "😡", "🥶", "💰", "🏆", "❌", "✅", "⚠️", "💯")
    data class Look(val label: String, val font: String, val color: String, val stroke: String, val strokeW: Int, val box: String, val shadow: Boolean, val upper: Boolean)
    val LOOKS = linkedMapOf(
        "beast" to Look("Yellow punch", "Arial Black", "#ffe11a", "#000000", 16, "", true, true),
        "white" to Look("White outline", "Arial Black", "#ffffff", "#000000", 14, "", true, true),
        "red" to Look("Red box", "Arial Black", "#ffffff", "#000000", 0, "#e8173c", false, true),
        "clean" to Look("White box", "Montserrat", "#111111", "#000000", 0, "#ffffff", false, false),
        "neon" to Look("Pink outline", "Impact", "#ffffff", "#ff3d6e", 10, "", true, true),
        "gta" to Look("GTA", "Pricedown Black", "#ffe11a", "#000000", 14, "", true, true),
    )
    fun applyLook(l: JSONObject, k: String) {
        val o = LOOKS[k] ?: return
        l.put("font", o.font).put("color", o.color).put("stroke", o.stroke).put("strokeW", o.strokeW).put("box", o.box).put("shadow", if (o.shadow) 1 else 0).put("upper", o.upper)
    }

    fun uid() = (1..7).map { "abcdefghijklmnopqrstuvwxyz0123456789".random() }.joinToString("")
    fun defaults(): JSONObject = JSONObject().put("v", 2).put("t", 0).put("layout", "")
        .put("bg", JSONObject().put("kind", "frame").put("src", "").put("nw", 1920).put("nh", 1080).put("z", 1).put("ox", 0.5).put("oy", 0.5)
            .put("bright", 1.05).put("contrast", 1.1).put("sat", 1.15).put("blur", 0).put("vignette", 0.35).put("color", "#101018"))
        .put("layers", JSONArray())

    fun newPhoto(src: String, nw: Int, nh: Int, x: Double = 540.0, y: Double = 1300.0, w: Double = 620.0, h: Double? = null, shape: String = "round", border: Int = 10, bcolor: String = "#ffffff"): JSONObject {
        val ar = if (nw > 0 && nh > 0) nh.toDouble() / nw else 0.5625
        return JSONObject().put("kind", "photo").put("id", uid()).put("src", src).put("nw", if (nw > 0) nw else 1600).put("nh", if (nh > 0) nh else 900)
            .put("x", x).put("y", y).put("w", w).put("h", h ?: Math.round(w * ar).toDouble()).put("rot", 0).put("shape", shape).put("radius", 0.08)
            .put("border", border).put("bcolor", bcolor).put("shadow", 1).put("flip", false).put("crop", JSONObject().put("z", 1).put("ox", 0.5).put("oy", 0.5))
    }
    fun newText(t: String, look: String = "beast", x: Double = 540.0, y: Double = 380.0, size: Double = 120.0, rot: Double = 0.0): JSONObject =
        JSONObject().put("kind", "text").put("id", uid()).put("text", t).put("x", x).put("y", y).put("size", size).put("rot", rot).put("maxW", 960).put("align", "center").also { applyLook(it, look) }
    fun newSticker(t: String, x: Double = 760.0, y: Double = 900.0, size: Double? = null, color: String = "#ff2a2a"): JSONObject {
        val emoji = SHAPES.none { it.first == t }
        return JSONObject().put("kind", "sticker").put("id", uid()).put("t", t).put("emoji", emoji).put("x", x).put("y", y)
            .put("size", size ?: if (emoji) 220.0 else 300.0).put("rot", 0).put("color", color).put("lw", 22)
    }

    /** The layouts, given a frame of the clip and the hook text. */
    val LAYOUTS = linkedMapOf<String, Pair<String, (String, Int, Int, String) -> List<JSONObject>>>(
        "hook" to ("Big hook" to { _, _, _, h -> listOf(newText(h, "beast", y = 360.0, size = 132.0)) }),
        "reaction" to ("Reaction bubble" to { s, w, h, t -> listOf(newPhoto(s, w, h, 300.0, 1480.0, 460.0, 460.0, "circle", 14), newText(t, "white", y = 330.0, size = 118.0), newSticker("😱", 800.0, 1450.0, 240.0)) }),
        "split" to ("Top and bottom" to { s, w, h, t -> listOf(newPhoto(s, w, h, 540.0, 520.0, 1080.0, 820.0, "rect", 0), newText(t, "red", y = 1000.0, size = 92.0)) }),
        "versus" to ("Side by side" to { s, w, h, t -> listOf(newPhoto(s, w, h, 290.0, 1050.0, 500.0, 700.0, "round", 10, "#ffffff"), newPhoto(s, w, h, 790.0, 1050.0, 500.0, 700.0, "round", 10, "#ffe11a"), newSticker("arrow", 540.0, 1520.0, 260.0, "#ffe11a"), newText(t, "beast", y = 420.0, size = 110.0)) }),
        "card" to ("Photo card" to { s, w, h, t -> listOf(newPhoto(s, w, h, 540.0, 980.0, 900.0, 506.0, "round", 12), newText(t, "clean", y = 560.0, size = 82.0), newSticker("🔥", 900.0, 1270.0, 200.0)) }),
        "headline" to ("Headline bar" to { _, _, _, t -> listOf(newText(t, "red", y = 1500.0, size = 100.0, rot = -3.0)) }),
        "clean" to ("Clean frame" to { _, _, _, _ -> emptyList() }),
    )

    // ------------------------------------------------------------------ measuring
    private fun font(l: JSONObject) { text.typeface = Overlay.face(l.optString("font")); text.textSize = l.optDouble("size", 120.0).toFloat() }
    fun lines(l: JSONObject): List<String> {
        font(l)
        val raw = l.optString("text").let { if (l.optBoolean("upper")) it.uppercase() else it }
        val box = l.optString("box").isNotEmpty()
        val mw = l.optDouble("maxW", 960.0).toFloat() - (if (box) l.optDouble("size", 120.0).toFloat() * 0.7f else l.optDouble("strokeW", 0.0).toFloat())
        val out = mutableListOf<String>()
        raw.split("\n").forEach { par ->
            var cur = ""
            par.split(Regex("\\s+")).filter { it.isNotEmpty() }.forEach { w ->
                val nx = if (cur.isEmpty()) w else "$cur $w"
                if (cur.isNotEmpty() && text.measureText(nx) > mw) { out += cur; cur = w } else cur = nx
            }
            out += cur
        }
        return out
    }
    fun size(l: JSONObject): FloatArray = when (l.optString("kind")) {
        "photo" -> floatArrayOf(l.optDouble("w").toFloat(), l.optDouble("h").toFloat())
        "text" -> {
            val ls = lines(l); font(l)
            val s = l.optDouble("size", 120.0).toFloat(); val box = l.optString("box").isNotEmpty(); val sw = l.optDouble("strokeW", 0.0).toFloat()
            val w = max(10f, ls.maxOf { text.measureText(it) })
            floatArrayOf(w + if (box) s * 0.7f else sw, ls.size * s * 1.08f + if (box) s * 0.45f else sw)
        }
        else -> { val s = l.optDouble("size", 220.0).toFloat(); floatArrayOf(s, if (l.optString("t") == "arrow") s * 0.55f else s) }
    }

    // ------------------------------------------------------------------ drawing
    private fun shape(l: JSONObject): Path {
        val w = l.optDouble("w").toFloat(); val h = l.optDouble("h").toFloat(); val p = Path(); val r = RectF(-w / 2, -h / 2, w / 2, h / 2)
        when (l.optString("shape")) {
            "circle" -> p.addOval(r, Path.Direction.CW)
            "round" -> { val rr = min(min(w, h) * l.optDouble("radius", 0.08).toFloat(), min(w, h) / 2); p.addRoundRect(r, rr, rr, Path.Direction.CW) }
            else -> p.addRect(r, Path.Direction.CW)
        }
        return p
    }
    /** The picture fills the box; z zooms in, ox/oy pick the part that shows. */
    private fun cover(c: Canvas, bm: Bitmap, x: Float, y: Float, w: Float, h: Float, z: Float, ox: Float, oy: Float, p: Paint = fill) {
        val iw = bm.width.toFloat(); val ih = bm.height.toFloat(); if (iw <= 0) return
        val s = max(w / iw, h / ih) * z; val dw = iw * s; val dh = ih * s
        val l = x - (dw - w) * ox; val t = y - (dh - h) * oy
        c.drawBitmap(bm, null, RectF(l, t, l + dw, t + dh), p)
    }
    private fun matrix(b: Float, ct: Float, s: Float): ColorMatrix {
        val m = ColorMatrix(floatArrayOf(b, 0f, 0f, 0f, 0f, 0f, b, 0f, 0f, 0f, 0f, 0f, b, 0f, 0f, 0f, 0f, 0f, 1f, 0f))
        val o = (0.5f - 0.5f * ct) * 255
        m.postConcat(ColorMatrix(floatArrayOf(ct, 0f, 0f, 0f, o, 0f, ct, 0f, 0f, o, 0f, 0f, ct, 0f, o, 0f, 0f, 0f, 1f, 0f)))
        m.postConcat(ColorMatrix().apply { setSaturation(s) })
        return m
    }
    private fun background(c: Canvas, d: JSONObject, images: Map<String, Bitmap>) {
        val bg = d.optJSONObject("bg") ?: JSONObject()
        fill.color = Overlay.color(bg.optString("color"), 0xFF000000.toInt()); fill.colorFilter = null
        c.drawRect(0f, 0f, CW, CH, fill)
        val bm = images[bg.optString("src")]
        if (bg.optString("kind") == "color" || bm == null) return
        val p = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
        p.colorFilter = ColorMatrixColorFilter(matrix(bg.optDouble("bright", 1.0).toFloat(), bg.optDouble("contrast", 1.0).toFloat(), bg.optDouble("sat", 1.0).toFloat()))
        val z = bg.optDouble("z", 1.0).toFloat(); val ox = bg.optDouble("ox", 0.5).toFloat(); val oy = bg.optDouble("oy", 0.5).toFloat()
        val blur = bg.optDouble("blur", 0.0).toFloat()
        if (blur > 0.5f) {
            // blur by drawing small and stretching back up, twice, which looks close to a real blur
            val key = "${bg.optString("src")}|$blur|$z|$ox|$oy"
            val small = blurCache.getOrPut(key) {
                if (blurCache.size > 6) blurCache.clear()
                val f = 1f + blur / 2.2f
                val a = Bitmap.createBitmap(max(8, (CW / f).toInt()), max(8, (CH / f).toInt()), Bitmap.Config.ARGB_8888)
                Canvas(a).apply { scale(a.width / CW, a.height / CH); cover(this, bm, 0f, 0f, CW, CH, z, ox, oy) }
                val b2 = Bitmap.createScaledBitmap(a, max(4, a.width / 2), max(4, a.height / 2), true)
                Bitmap.createScaledBitmap(b2, a.width, a.height, true)
            }
            c.drawBitmap(small, null, RectF(0f, 0f, CW, CH), p)
        } else cover(c, bm, 0f, 0f, CW, CH, z, ox, oy, p)
        val v = bg.optDouble("vignette", 0.0).toFloat()
        if (v > 0f) {
            val g = Paint(); g.shader = RadialGradient(CW / 2, CH / 2, CH * 0.75f, intArrayOf(0, 0, android.graphics.Color.argb((v * 255).toInt().coerceIn(0, 255), 0, 0, 0)), floatArrayOf(0f, 0.333f, 1f), Shader.TileMode.CLAMP)
            c.drawRect(0f, 0f, CW, CH, g)
        }
    }
    private fun layer(c: Canvas, l: JSONObject, images: Map<String, Bitmap>) {
        c.save(); c.translate(l.optDouble("x").toFloat(), l.optDouble("y").toFloat()); c.rotate(l.optDouble("rot", 0.0).toFloat())
        when (l.optString("kind")) {
            "photo" -> {
                val w = l.optDouble("w").toFloat(); val h = l.optDouble("h").toFloat(); val path = shape(l)
                if (l.optInt("shadow", 0) != 0 || l.optBoolean("shadow")) {
                    val sp = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF000000.toInt(); setShadowLayer(40f, 0f, 14f, 0x8C000000.toInt()) }
                    c.drawPath(path, sp)
                }
                c.save(); c.clipPath(path)
                if (l.optBoolean("flip")) c.scale(-1f, 1f)
                val bm = images[l.optString("src")]
                val cr = l.optJSONObject("crop") ?: JSONObject()
                fill.colorFilter = null
                if (bm != null) cover(c, bm, -w / 2, -h / 2, w, h, cr.optDouble("z", 1.0).toFloat(), cr.optDouble("ox", 0.5).toFloat(), cr.optDouble("oy", 0.5).toFloat())
                else { fill.color = 0xFF333333.toInt(); c.drawRect(-w / 2, -h / 2, w / 2, h / 2, fill) }
                c.restore()
                val bw = l.optDouble("border", 0.0).toFloat()
                if (bw > 0) { stroke.strokeWidth = bw; stroke.color = Overlay.color(l.optString("bcolor"), -1); stroke.pathEffect = null; c.drawPath(path, stroke) }
            }
            "text" -> {
                val ls = lines(l); font(l)
                val s = l.optDouble("size", 120.0).toFloat(); val lh = s * 1.08f; val y0 = -lh * (ls.size - 1) / 2
                text.textAlign = Paint.Align.CENTER
                val fm = text.fontMetrics; val base = -(fm.ascent + fm.descent) / 2
                if (l.optString("box").isNotEmpty()) {
                    val sz = size(l); fill.color = Overlay.color(l.optString("box"), -1); fill.colorFilter = null; val r = s * 0.18f
                    c.drawRoundRect(-sz[0] / 2, -sz[1] / 2, sz[0] / 2, sz[1] / 2, r, r, fill)
                }
                font(l); text.textAlign = Paint.Align.CENTER
                ls.forEachIndexed { i, line ->
                    val y = y0 + i * lh + base
                    if (l.optInt("shadow", 0) != 0 || l.optBoolean("shadow")) { text.style = Paint.Style.FILL; text.color = 0x99000000.toInt(); c.drawText(line, 6f, y + 10f, text) }
                    val sw = l.optDouble("strokeW", 0.0).toFloat()
                    if (sw > 0) { text.style = Paint.Style.STROKE; text.strokeWidth = sw; text.strokeJoin = Paint.Join.ROUND; text.color = Overlay.color(l.optString("stroke"), 0xFF000000.toInt()); c.drawText(line, 0f, y, text) }
                    text.style = Paint.Style.FILL; text.color = Overlay.color(l.optString("color"), -1); c.drawText(line, 0f, y, text)
                }
            }
            else -> {
                val s = l.optDouble("size", 220.0).toFloat()
                if (l.optBoolean("emoji", true) && SHAPES.none { it.first == l.optString("t") }) {
                    text.typeface = android.graphics.Typeface.DEFAULT; text.textSize = s; text.textAlign = Paint.Align.CENTER; text.style = Paint.Style.FILL; text.color = 0xFF000000.toInt()
                    val fm = text.fontMetrics; c.drawText(l.optString("t"), 0f, -(fm.ascent + fm.descent) / 2, text)
                } else {
                    val lw = l.optDouble("lw", 22.0).toFloat(); val col = Overlay.color(l.optString("color"), 0xFFFF2A2A.toInt())
                    stroke.color = col; stroke.strokeWidth = lw; stroke.pathEffect = null; stroke.setShadowLayer(12f, 0f, 0f, 0x73000000)
                    when (l.optString("t")) {
                        "ring" -> c.drawOval(-s / 2 + lw, -s / 2 + lw, s / 2 - lw, s / 2 - lw, stroke)
                        "box" -> c.drawRect(-s / 2 + lw / 2, -s / 2 + lw / 2, s / 2 - lw / 2, s / 2 - lw / 2, stroke)
                        "x" -> { c.drawLine(-s / 2 + lw, -s / 2 + lw, s / 2 - lw, s / 2 - lw, stroke); c.drawLine(s / 2 - lw, -s / 2 + lw, -s / 2 + lw, s / 2 - lw, stroke) }
                        else -> {
                            val h = s * 0.55f; val p = Path()
                            p.moveTo(-s / 2, -h * 0.18f); p.lineTo(s * 0.12f, -h * 0.18f); p.lineTo(s * 0.12f, -h / 2); p.lineTo(s / 2, 0f)
                            p.lineTo(s * 0.12f, h / 2); p.lineTo(s * 0.12f, h * 0.18f); p.lineTo(-s / 2, h * 0.18f); p.close()
                            fill.color = col; fill.colorFilter = null; fill.setShadowLayer(12f, 0f, 0f, 0x73000000); c.drawPath(p, fill); fill.clearShadowLayer()
                        }
                    }
                    stroke.clearShadowLayer()
                }
            }
        }
        c.restore()
    }

    /** Draws the whole design on [c] (in cover units). [sel] adds the selection box; [k] is screen px per cover unit. */
    fun render(c: Canvas, d: JSONObject, images: Map<String, Bitmap>, sel: JSONObject? = null, crop: Boolean = false, k: Float = 1f) {
        background(c, d, images)
        val a = d.optJSONArray("layers") ?: JSONArray()
        for (i in 0 until a.length()) a.optJSONObject(i)?.let { if (!it.optBoolean("hidden")) layer(c, it, images) }
        if (sel == null) return
        val sz = size(sel); val w = sz[0]; val h = sz[1]; val u = 1f / k
        c.save(); c.translate(sel.optDouble("x").toFloat(), sel.optDouble("y").toFloat()); c.rotate(sel.optDouble("rot", 0.0).toFloat())
        stroke.clearShadowLayer(); stroke.color = if (crop) 0xFFFFD400.toInt() else 0xFF2EE6C8.toInt(); stroke.strokeWidth = 2.5f * u
        stroke.pathEffect = if (crop) DashPathEffect(floatArrayOf(12 * u, 8 * u), 0f) else null
        c.drawRect(-w / 2, -h / 2, w / 2, h / 2, stroke); stroke.pathEffect = null
        if (!crop) {
            val r = 11 * u
            c.drawLine(0f, -h / 2, 0f, -h / 2 - 46 * u, stroke)
            listOf(w / 2 to h / 2, 0f to -h / 2 - 46 * u).forEach { (x, y) ->
                fill.color = -1; fill.colorFilter = null; c.drawCircle(x, y, r, fill); c.drawCircle(x, y, r, stroke)
            }
        }
        c.restore()
    }

    fun toJpeg(d: JSONObject, images: Map<String, Bitmap>): ByteArray {
        val bm = Bitmap.createBitmap(CW.toInt(), CH.toInt(), Bitmap.Config.ARGB_8888)
        render(Canvas(bm), d, images)
        val out = java.io.ByteArrayOutputStream()
        bm.compress(Bitmap.CompressFormat.JPEG, 92, out); bm.recycle()
        return out.toByteArray()
    }
}
