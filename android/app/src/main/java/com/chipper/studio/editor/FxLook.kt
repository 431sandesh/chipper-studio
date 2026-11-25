package com.chipper.studio.editor

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * How the visual effects active at one moment change the picture, worked out the same way as the
 * laptop's ffmpeg filters so the preview matches the render.
 */
class FxLook {
    var scale = 1f; var dx = 0f; var dy = 0f                 // zoom punch / shake (canvas px)
    var sat = 1f; var r = 1f; var g = 1f; var b = 1f          // colour
    var contrast = 1f; var bright = 0f
    var shift = 0f; var noise = 0f                            // glitch / deep fry (canvas px, 0..1)
    var vignette = 0f; var flash = 0f; var flashColor = -1
    val none get() = scale == 1f && dx == 0f && dy == 0f && sat == 1f && r == 1f && g == 1f && b == 1f && contrast == 1f && bright == 0f && shift == 0f && noise == 0f && vignette == 0f && flash == 0f

    companion object {
        val NONE = FxLook()

        fun at(m: EdModel, tAbs: Double): FxLook {
            val arr = m.scene.optJSONArray("fx") ?: return NONE
            if (arr.length() == 0) return NONE
            val t = tAbs - m.start
            val o = FxLook()
            for (i in 0 until arr.length()) {
                val f = arr.optJSONObject(i) ?: continue
                val s = f.optDouble("start", 0.0); val d = f.optDouble("dur", 0.5)
                if (t < s || t > s + d) continue
                val a = f.optDouble("amt", 0.7).toFloat()
                when (f.optString("type")) {
                    "zoom" -> o.scale *= 1 + 0.35f * a
                    "shake" -> { val mm = max(4f, 26 * a); o.scale *= (1080 + 2 * mm) / 1080f; o.dx += mm * sin(t * 61).toFloat(); o.dy += mm * cos(t * 53).toFloat() }
                    "flash" -> { o.flash = max(o.flash, min(1f, 0.4f + 0.6f * a) * (1 - ((t - s) / d).toFloat()).coerceIn(0f, 1f)); o.flashColor = Overlay.color(f.optString("color"), -1) }
                    "bw" -> { o.sat = 0f; o.contrast *= 1 + 0.3f * a }
                    "red" -> { o.r *= 1.1f; o.g *= 1 - 0.5f * a; o.b *= 1 - 0.5f * a; o.vignette = max(o.vignette, 0.55f) }
                    "glitch" -> { o.shift = max(o.shift, max(4f, 18 * a)); o.noise = max(o.noise, 0.12f * a) }
                    "blue" -> { o.r *= 1 - 0.35f * a; o.g *= 1 - 0.15f * a; o.b *= 1 + 0.15f * a; o.sat *= 1 - 0.5f * a; o.bright -= 0.05f * a }
                    "dark" -> { o.bright -= 0.12f * a; o.contrast *= 1 + 0.15f * a; o.vignette = max(o.vignette, 0.6f + 0.3f * a) }
                    "fry" -> { o.sat *= 1 + 2 * a; o.contrast *= 1 + 0.6f * a; o.noise = max(o.noise, 0.08f * a) }
                }
            }
            return o
        }

        private val p = Paint(Paint.ANTI_ALIAS_FLAG)

        /** The parts drawn over everything: dark corners and the flash. [w],[h] are the preview's size in px. */
        fun drawTop(c: Canvas, fx: FxLook, w: Float, h: Float) {
            if (fx.vignette > 0f) {
                p.shader = RadialGradient(w / 2, h / 2, h * 0.75f, intArrayOf(0, 0, android.graphics.Color.argb((fx.vignette * 255).toInt().coerceIn(0, 255), 0, 0, 0)),
                    floatArrayOf(0f, 0.33f, 1f), Shader.TileMode.CLAMP)
                c.drawRect(0f, 0f, w, h, p); p.shader = null
            }
            if (fx.flash > 0f) { p.color = fx.flashColor; p.alpha = (fx.flash * 255).toInt().coerceIn(0, 255); c.drawRect(0f, 0f, w, h, p) }
        }
    }
}
