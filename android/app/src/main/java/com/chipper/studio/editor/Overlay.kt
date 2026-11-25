package com.chipper.studio.editor

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.Typeface
import org.json.JSONObject
import kotlin.math.max
import kotlin.math.min

/** Draws everything on top of the video: images, text, lower thirds, captions, the hook, and the selection box. */
object Overlay {
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeJoin = Paint.Join.ROUND }
    private val faces = HashMap<String, Typeface>()

    fun face(name: String?, bold: Boolean = true): Typeface {
        val key = (name ?: "") + bold
        return faces.getOrPut(key) {
            val n = (name ?: "").lowercase()
            val fam = when {
                n.contains("serif") && !n.contains("sans") -> "serif"
                n.contains("mono") || n.contains("courier") -> "monospace"
                n.contains("condensed") || n.contains("bebas") || n.contains("anton") || n.contains("oswald") || n.contains("impact") -> "sans-serif-condensed"
                else -> "sans-serif-black"
            }
            Typeface.create(fam, if (bold) Typeface.BOLD else Typeface.NORMAL)
        }
    }

    fun color(s: String?, def: Int): Int = try {
        val v = (s ?: "").trim()
        when {
            v.startsWith("#") && v.length == 4 -> Color.parseColor("#" + v.drop(1).map { "$it$it" }.joinToString(""))
            v.startsWith("#") -> Color.parseColor(v)
            v.startsWith("&H") && v.length >= 10 -> { val b = v.substring(4, 6).toInt(16); val g = v.substring(6, 8).toInt(16); val r = v.substring(8, 10).toInt(16); Color.rgb(r, g, b) }
            else -> def
        }
    } catch (e: Exception) { def }

    fun draw(c: Canvas, m: EdModel, tAbs: Double, k: Float, images: Map<String, Bitmap>, showSel: Boolean) {
        val tRel = tAbs - m.start
        for (l in m.order()) {
            if (!m.visible(l, tRel)) continue
            when (l.optString("kind")) {
                "image" -> images[l.optString("file")]?.let { drawImage(c, m, l, it, k) }
                "text" -> drawText(c, l, k)
                "third" -> drawPrims(c, l, k, tRel)
                "captions" -> drawCaptions(c, m, l, tAbs, k)
                "hook" -> drawHook(c, m, l, k)
            }
        }
        if (showSel) m.selected()?.let { l ->
            val ar = images[l.optString("file")]?.let { it.width.toFloat() / it.height } ?: 1f
            val b = m.bounds(l, ar)
            stroke.color = 0xFF2EE6C8.toInt(); stroke.strokeWidth = 3f; stroke.pathEffect = null
            c.drawRect(b[0] * k, b[1] * k, (b[0] + b[2]) * k, (b[1] + b[3]) * k, stroke)
            fill.color = 0xFF2EE6C8.toInt(); c.drawCircle((b[0] + b[2]) * k, (b[1] + b[3]) * k, 10f, fill)
        }
    }

    private fun drawImage(c: Canvas, m: EdModel, l: JSONObject, bm: Bitmap, k: Float) {
        val b = m.bounds(l, bm.width.toFloat() / bm.height)
        val cr = l.optJSONArray("crop")
        val src = if (cr != null) Rect((cr.optDouble(0) * bm.width).toInt(), (cr.optDouble(1) * bm.height).toInt(),
            ((cr.optDouble(0) + cr.optDouble(2, 1.0)) * bm.width).toInt(), ((cr.optDouble(1) + cr.optDouble(3, 1.0)) * bm.height).toInt()) else null
        val dst = RectF(b[0] * k, b[1] * k, (b[0] + b[2]) * k, (b[1] + b[3]) * k)
        c.save()
        when (l.optString("shape", "rect")) {
            "circle" -> c.clipPath(Path().apply { addOval(dst, Path.Direction.CW) })
            "round", "squircle" -> { val r = min(dst.width(), dst.height()) * l.optDouble("radius", 0.12).toFloat(); c.clipPath(Path().apply { addRoundRect(dst, r, r, Path.Direction.CW) }) }
        }
        fill.alpha = (l.optDouble("opacity", 1.0) * 255).toInt()
        c.drawBitmap(bm, src, dst, fill); fill.alpha = 255
        c.restore()
    }

    fun drawText(c: Canvas, l: JSONObject, k: Float) {
        val s = l.optDouble("size", 70.0).toFloat() * k
        fill.typeface = face(l.optString("font")); fill.textSize = s; fill.textAlign = Paint.Align.CENTER
        stroke.typeface = fill.typeface; stroke.textSize = s; stroke.textAlign = Paint.Align.CENTER
        val lines = l.optString("text").split("\n")
        val lh = s * 1.2f; val x = l.optDouble("x").toFloat() * k; val y0 = l.optDouble("y").toFloat() * k - lh * (lines.size - 1) / 2
        val a = (l.optDouble("opacity", 1.0) * 255).toInt()
        lines.forEachIndexed { i, t ->
            val y = y0 + i * lh; val base = y - (fill.ascent() + fill.descent()) / 2; val w = fill.measureText(t)
            if (l.optBoolean("box")) { fill.color = color(l.optString("bcolor"), Color.BLACK); fill.alpha = (a * 0.75f).toInt(); c.drawRect(x - w / 2 - s * 0.25f, y - lh / 2, x + w / 2 + s * 0.25f, y + lh / 2, fill) }
            else if (l.optDouble("outline", 1.0) > 0) { stroke.color = color(l.optString("stroke"), Color.BLACK); stroke.alpha = a; stroke.strokeWidth = max(2f, s / 5.5f * l.optDouble("outline", 1.0).toFloat()); c.drawText(t, x, base, stroke) }
            fill.color = color(l.optString("color"), Color.WHITE); fill.alpha = a; c.drawText(t, x, base, fill)
        }
        fill.alpha = 255
    }

    private fun drawPrims(c: Canvas, l: JSONObject, k: Float, tRel: Double) {
        val ps = l.optJSONArray("prims") ?: return
        val s = l.optDouble("scale", 1.0).toFloat(); val X = l.optDouble("x").toFloat(); val Y = l.optDouble("y").toFloat()
        for (i in 0 until ps.length()) {
            val p = ps.getJSONObject(i)
            val alpha = (p.optDouble("alpha", 1.0) * 255).toInt()
            when (p.optString("t")) {
                "rr", "poly" -> {
                    val path = Path()
                    if (p.optString("t") == "rr") {
                        val r = RectF((X + p.optDouble("x").toFloat() * s) * k, (Y + p.optDouble("y").toFloat() * s) * k,
                            (X + (p.optDouble("x") + p.optDouble("w")).toFloat() * s) * k, (Y + (p.optDouble("y") + p.optDouble("h")).toFloat() * s) * k)
                        val rad = min(p.optDouble("r", 0.0).toFloat() * s * k, min(r.width(), r.height()) / 2)
                        path.addRoundRect(r, rad, rad, Path.Direction.CW)
                    } else p.optJSONArray("pts")?.let { a ->
                        for (j in 0 until a.length()) { val q = a.getJSONArray(j); val px = (X + q.getDouble(0).toFloat() * s) * k; val py = (Y + q.getDouble(1).toFloat() * s) * k; if (j == 0) path.moveTo(px, py) else path.lineTo(px, py) }
                        path.close()
                    }
                    if (alpha > 0) { fill.color = color(p.optString("color"), Color.BLACK); fill.alpha = alpha; c.drawPath(path, fill) }
                    if (p.optDouble("bw", 0.0) > 0) { stroke.color = color(p.optString("bc"), Color.WHITE); stroke.strokeWidth = p.optDouble("bw").toFloat() * 2 * s * k; c.drawPath(path, stroke) }
                }
                else -> {
                    fill.typeface = face(p.optString("font"), p.optInt("weight", 900) >= 600); fill.textSize = p.optDouble("size", 30.0).toFloat() * s * k
                    fill.textAlign = when (p.optInt("an", 4)) { 5 -> Paint.Align.CENTER; 6 -> Paint.Align.RIGHT; else -> Paint.Align.LEFT }
                    fill.textSkewX = if (p.optBoolean("italic")) -0.2f else 0f
                    val x = (X + p.optDouble("x").toFloat() * s) * k; val y = (Y + p.optDouble("y").toFloat() * s) * k - (fill.ascent() + fill.descent()) / 2
                    if (p.optDouble("bw", 0.0) > 0) { stroke.typeface = fill.typeface; stroke.textSize = fill.textSize; stroke.textAlign = fill.textAlign; stroke.color = color(p.optString("bc"), Color.BLACK); stroke.strokeWidth = p.optDouble("bw").toFloat() * 2 * s * k; c.drawText(p.optString("text"), x, y, stroke) }
                    fill.color = color(p.optString("color"), Color.WHITE); fill.alpha = alpha; c.drawText(p.optString("text"), x, y, fill); fill.textSkewX = 0f
                }
            }
        }
        fill.alpha = 255
    }

    private fun drawCaptions(c: Canvas, m: EdModel, l: JSONObject, t: Double, k: Float) {
        val g = m.groupAt(t) ?: return
        val st = m.capStyle()
        val up = st.optBoolean("upper")
        val toks = g.map { if (up) m.words[it].t.uppercase() else m.words[it].t }
        val cur = g.indexOfFirst { t >= m.words[it].a && t < m.words[it].b + 0.05 }
        val sz = st.optDouble("size", 90.0).toFloat() * l.optDouble("scale", 1.0).toFloat() * k
        fill.typeface = face(st.optString("font")); fill.textSize = sz; fill.textAlign = Paint.Align.LEFT
        stroke.typeface = fill.typeface; stroke.textSize = sz; stroke.textAlign = Paint.Align.LEFT
        val sp = fill.measureText(" "); val ws = toks.map { fill.measureText(it) }; val maxW = 1020 * k
        val lines = mutableListOf(mutableListOf<Int>()); var lw = 0f
        toks.indices.forEach { i ->
            val add = (if (lines.last().isNotEmpty()) sp else 0f) + ws[i]
            if (lw + add > maxW && lines.last().isNotEmpty()) { lines += mutableListOf<Int>(); lw = 0f }
            lw += (if (lines.last().isNotEmpty()) sp else 0f) + ws[i]; lines.last() += i
        }
        val lh = sz * 1.12f; val y0 = l.optDouble("y").toFloat() * k - (lines.size - 1) * lh / 2
        val col = color(st.optString("color"), Color.WHITE); val hi = color(st.optString("hi"), Color.YELLOW)
        val box = st.opt("box")
        lines.forEachIndexed { li, ln ->
            val tot = ln.sumOf { ws[it].toDouble() }.toFloat() + sp * (ln.size - 1)
            val cy = y0 + li * lh; var cx = l.optDouble("x").toFloat() * k - tot / 2
            val base = cy - (fill.ascent() + fill.descent()) / 2
            if (box is String && box.isNotEmpty() || box == true) { fill.color = if (box is String) color(box, 0xB3000000.toInt()) else 0xB3000000.toInt(); c.drawRect(cx - sz * 0.2f, cy - sz * 0.62f, cx + tot + sz * 0.2f, cy + sz * 0.62f, fill) }
            ln.forEach { i ->
                val on = st.optString("mode") != "plain" && i == cur
                if (box == null || box == false) { stroke.color = Color.BLACK; stroke.strokeWidth = st.optDouble("outline", 7.0).toFloat() * 2 * l.optDouble("scale", 1.0).toFloat() * k; c.drawText(toks[i], cx, base, stroke) }
                fill.color = if (on) hi else col; c.drawText(toks[i], cx, base, fill)
                cx += ws[i] + sp
            }
        }
    }

    private fun drawHook(c: Canvas, m: EdModel, l: JSONObject, k: Float) {
        val txt = m.hook.trim(); if (txt.isEmpty()) return
        val look = l.optString("look", "box"); val s = l.optDouble("scale", 1.0).toFloat()
        val acc = color(l.optString("accent"), 0xFFFF3D6E.toInt())
        val fs = (if (look == "outline") 84 * 1.15f else if (look == "sticker") 84 * 0.82f else 84f) * s * k
        fill.typeface = face("Impact"); fill.textSize = fs; fill.textAlign = Paint.Align.CENTER
        stroke.typeface = fill.typeface; stroke.textSize = fs; stroke.textAlign = Paint.Align.CENTER
        val lines = wrap(txt.uppercase(), fill, 960 * k)
        val lh = fs * 1.15f; val x = l.optDouble("x", 540.0).toFloat() * k; val y0 = l.optDouble("y").toFloat() * k - lh * (lines.size - 1) / 2
        if (look == "banner") { fill.color = acc; val bh = lines.size * lh + 46 * s * k; c.drawRect(0f, l.optDouble("y").toFloat() * k - bh / 2, W * k, l.optDouble("y").toFloat() * k + bh / 2, fill) }
        lines.forEachIndexed { i, t ->
            val y = y0 + i * lh; val base = y - (fill.ascent() + fill.descent()) / 2; val w = fill.measureText(t)
            when (look) {
                "box", "sticker" -> { fill.color = Color.WHITE; c.drawRoundRect(x - w / 2 - 22 * k, y - lh / 2, x + w / 2 + 22 * k, y + lh / 2, 10 * k, 10 * k, fill); fill.color = Color.BLACK }
                "neon" -> { stroke.color = acc; stroke.strokeWidth = 10 * k; c.drawText(t, x, base, stroke); fill.color = Color.WHITE }
                else -> { stroke.color = Color.BLACK; stroke.strokeWidth = 14 * s * k; c.drawText(t, x, base, stroke); fill.color = Color.WHITE }
            }
            c.drawText(t, x, base, fill)
        }
    }

    private fun wrap(t: String, p: Paint, maxW: Float): List<String> {
        val out = mutableListOf<String>()
        t.split("\n").forEach { para ->
            var cur = ""
            para.split(" ").filter { it.isNotBlank() }.forEach { w -> val nx = if (cur.isEmpty()) w else "$cur $w"; if (cur.isNotEmpty() && p.measureText(nx) > maxW) { out += cur; cur = w } else cur = nx }
            if (cur.isNotEmpty()) out += cur
        }
        return out.ifEmpty { listOf(t) }
    }
}
