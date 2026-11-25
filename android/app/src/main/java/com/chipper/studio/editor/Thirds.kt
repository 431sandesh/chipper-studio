package com.chipper.studio.editor

import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max

/** Lower thirds, built the same way as on the laptop so the render matches the preview. */
object Thirds {
    val TEMPLATES = listOf("darkpill" to "Dark pill", "slant" to "Slant card", "softpill" to "Soft pill", "tab" to "Tab card")
    data class Plat(val label: String, val word: String, val color: String)
    val PLATFORMS = linkedMapOf(
        "youtube" to Plat("YouTube", "YOUTUBE", "#ff0000"), "instagram" to Plat("Instagram", "INSTAGRAM", "#d62976"),
        "tiktok" to Plat("TikTok", "TIKTOK", "#111111"), "facebook" to Plat("Facebook", "FACEBOOK", "#1877f2"),
        "twitch" to Plat("Twitch", "TWITCH", "#9146ff"), "x" to Plat("X", "X", "#111111"),
    )
    val ANIMS = listOf("slide" to "Slide in", "slider" to "From the right", "rise" to "Rise up", "drop" to "Drop in", "glide" to "Slow glide", "wipe" to "Wipe", "fade" to "Fade", "none" to "None")

    private fun charW(c: Char): Double = when {
        c == ' ' -> 0.3
        c in 'A'..'Z' -> if (c in "MW") 0.98 else if (c in "IJ") 0.4 else 0.76
        c in 'a'..'z' -> if (c in "mw") 0.94 else if (c in "ijlft") 0.36 else 0.64
        c in '0'..'9' -> 0.66
        c == '@' -> 0.92
        c in "._,:;!'|" -> 0.34
        else -> 0.62
    }
    fun estW(t: String, size: Double, font: String = "Arial Black") = t.sumOf { charW(it) } * size * (if (font == "Arial Black") 1.0 else 0.84)

    private class P {
        val a = JSONArray()
        fun R(x: Double, y: Double, w: Double, h: Double, r: Double, color: String, alpha: Double = 1.0, bw: Double = 0.0, bc: String = "#ffffff", blur: Double = 0.0) =
            a.put(JSONObject().put("t", "rr").put("x", x).put("y", y).put("w", w).put("h", h).put("r", r).put("color", color).put("alpha", alpha).put("bw", bw).put("bc", bc).put("blur", blur))
        fun G(pts: List<Pair<Double, Double>>, color: String, alpha: Double = 1.0, bw: Double = 0.0, bc: String = "#ffffff", blur: Double = 0.0) =
            a.put(JSONObject().put("t", "poly").put("pts", JSONArray(pts.map { JSONArray(listOf(Math.round(it.first), Math.round(it.second))) })).put("color", color).put("alpha", alpha).put("bw", bw).put("bc", bc).put("blur", blur))
        fun T(x: Double, y: Double, text: String, size: Double, color: String, an: Int = 4, font: String = "Arial", weight: Int = 700, italic: Boolean = false) =
            a.put(JSONObject().put("t", "text").put("x", x).put("y", y).put("text", text).put("size", size).put("color", color).put("an", an).put("font", font).put("alpha", 1).put("bw", 0).put("bc", "#000000").put("italic", italic).put("weight", weight))
    }

    private fun glyph(p: P, plat: String, cx: Double, cy: Double, s: Double, fg: String, bg: String) {
        when (plat) {
            "youtube" -> { p.R(cx - s / 2, cy - s * 0.35, s, s * 0.7, s * 0.2, fg); p.G(listOf(cx - s * 0.12 to cy - s * 0.18, cx + s * 0.2 to cy, cx - s * 0.12 to cy + s * 0.18), bg) }
            "instagram" -> { p.R(cx - s * 0.44, cy - s * 0.44, s * 0.88, s * 0.88, s * 0.26, fg, alpha = 0.0, bw = s * 0.06, bc = fg); p.R(cx - s * 0.2, cy - s * 0.2, s * 0.4, s * 0.4, s * 0.2, fg, alpha = 0.0, bw = s * 0.06, bc = fg); p.R(cx + s * 0.18, cy - s * 0.3, s * 0.1, s * 0.1, s * 0.05, fg) }
            "tiktok" -> listOf(-s * 0.04 to "#25f4ee", s * 0.04 to "#fe2c55", 0.0 to fg).forEach { (d, c) ->
                p.R(cx + s * 0.02 + d, cy - s * 0.45, s * 0.14, s * 0.62, 0.0, c); p.R(cx - s * 0.32 + d, cy + s * 0.02, s * 0.4, s * 0.34, s * 0.17, c)
                p.G(listOf(cx + s * 0.16 + d to cy - s * 0.45, cx + s * 0.42 + d to cy - s * 0.3, cx + s * 0.42 + d to cy - s * 0.14, cx + s * 0.16 + d to cy - s * 0.26), c)
            }
            "facebook" -> p.T(cx + s * 0.04, cy + s * 0.08, "f", s * 1.15, fg, an = 5, font = "Arial Black")
            "twitch" -> { p.R(cx - s * 0.42, cy - s * 0.45, s * 0.84, s * 0.72, s * 0.08, fg); p.G(listOf(cx - s * 0.3 to cy + s * 0.26, cx - s * 0.02 to cy + s * 0.26, cx - s * 0.3 to cy + s * 0.5), fg)
                p.R(cx - s * 0.16, cy - s * 0.28, s * 0.1, s * 0.3, 0.0, bg); p.R(cx + s * 0.08, cy - s * 0.28, s * 0.1, s * 0.3, 0.0, bg) }
            else -> p.T(cx, cy + s * 0.04, "X", s * 0.95, fg, an = 5, font = "Arial Black")
        }
    }

    fun build(tpl: String, title0: String, sub: String, plat: String, font: String = "", color: String = "", size: Double = 1.0): JSONArray {
        val pf = PLATFORMS[plat] ?: PLATFORMS.getValue("youtube"); val col = pf.color; val head = sub.ifEmpty { "FOLLOW ME ON ${pf.word}" }
        val zs = size.coerceIn(0.6, 1.6); val title = title0.ifEmpty { "@yourusername" }
        val p = P()
        when (tpl) {
            "darkpill" -> {
                val wd = 138 + estW(title, 38 * zs, font.ifEmpty { "Arial" }) * 0.92 + 56
                p.R(0.0, 0.0, wd, 112.0, 56.0, "#262c38", alpha = 0.96); glyph(p, plat, 68.0, 56.0, 62.0, "#ffffff", "#262c38")
                p.T(138.0, 56.0, title, 38 * zs, color.ifEmpty { "#9aa1ad" }, weight = if (font.isNotEmpty()) 700 else 400, font = font.ifEmpty { "Arial" })
            }
            "slant" -> {
                val hw = estW(head, 17.0, "Arial"); val nw = estW(title, 32 * zs, font.ifEmpty { "Arial" }); val wd = 128 + max(hw, nw) + 76
                p.G(listOf(8.0 to 6.0, wd - 40 to 6.0, wd + 6 to 110.0, 30.0 to 110.0), "#000000", alpha = 0.22, blur = 8.0)
                p.G(listOf(0.0 to 0.0, wd - 46 to 0.0, wd to 104.0, 24.0 to 104.0), "#ffffff", bw = 3.0, bc = col)
                p.R(14.0, 6.0, 92.0, 92.0, 46.0, "#ffffff", bw = 4.0, bc = col); p.R(20.0, 12.0, 80.0, 80.0, 40.0, col)
                glyph(p, plat, 60.0, 52.0, 46.0, "#ffffff", col)
                p.T(128.0, 34.0, head.replace(Regex("^FOLLOW ME ON"), "FIND ME ON"), 17.0, "#222222", weight = 400)
                p.T(128.0, 70.0, title, 32 * zs, color.ifEmpty { "#111111" }, italic = font.isEmpty(), font = font.ifEmpty { "Arial" })
            }
            "softpill" -> {
                val wd = 122 + estW(title, 30 * zs, font.ifEmpty { "Arial" }) * 0.92 + 44
                p.R(56.0, 26.0, wd - 56, 62.0, 31.0, "#000000", alpha = 0.18, blur = 7.0); p.R(52.0, 18.0, wd - 52, 62.0, 31.0, "#f2f2f2")
                p.R(2.0, 6.0, 96.0, 96.0, 48.0, "#000000", alpha = 0.2, blur = 6.0); p.R(0.0, 0.0, 96.0, 96.0, 48.0, "#ffffff"); p.R(8.0, 8.0, 80.0, 80.0, 40.0, col)
                glyph(p, plat, 48.0, 48.0, 44.0, "#ffffff", col)
                p.T(122.0, 49.0, title, 30 * zs, color.ifEmpty { "#9a9a9a" }, weight = if (font.isNotEmpty()) 700 else 400, font = font.ifEmpty { "Arial" })
            }
            else -> {
                val hw = estW(head, 15.0, "Arial"); val nw = estW(title, 34 * zs, font.ifEmpty { "Arial Black" }); val wd = 106 + nw + 40
                p.R(62.0, 0.0, hw + 40, 30.0, 10.0, col); p.T(82.0, 15.0, head, 15.0, "#ffffff")
                p.R(40.0, 26.0, wd - 40, 74.0, 18.0, "#ffffff", alpha = 0.94, bw = 3.0, bc = col)
                p.R(0.0, 22.0, 82.0, 82.0, 41.0, col, bw = 4.0, bc = "#ffffff")
                glyph(p, plat, 41.0, 63.0, 40.0, "#ffffff", col)
                p.T(104.0, 63.0, title, 34 * zs, color.ifEmpty { col }, font = font.ifEmpty { "Arial Black" }, weight = 900)
            }
        }
        return p.a
    }
}
