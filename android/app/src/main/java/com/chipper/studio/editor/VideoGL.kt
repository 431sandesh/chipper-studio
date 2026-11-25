package com.chipper.studio.editor

import android.content.Context
import android.graphics.SurfaceTexture
import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.view.Surface
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

/** One video window to draw: where it goes on the 1080x1920 canvas and which part of the stream it shows. */
class Quad(
    val x: Float, val y: Float, val w: Float, val h: Float,
    val cx: Float, val cy: Float, val cw: Float, val ch: Float,
    val shape: Int, val radius: Float, val border: Float, val bcolor: Int, val alpha: Float,
    val blur: Float = 0f, val dim: Float = 0f,
)

/** What the GL view draws each frame: a background colour or blurred stream, then the video windows. */
class Frame(val bg: Int, val quads: List<Quad>, val fx: FxLook = FxLook.NONE, val time: Float = 0f)

/**
 * Draws the source video into several windows at once, each with its own crop, shape and border,
 * straight from the video decoder (no copying frames), so the preview stays smooth on a phone.
 */
class VideoGL(ctx: Context, private val onSurface: (Surface) -> Unit) : GLSurfaceView(ctx), GLSurfaceView.Renderer {
    @Volatile var frame: Frame = Frame(0xFF000000.toInt(), emptyList())
    private var st: SurfaceTexture? = null
    private var tex = 0
    private var prog = 0
    private val stm = FloatArray(16)
    private lateinit var vb: FloatBuffer
    private var vw = 1; private var vh = 1

    init {
        setEGLContextClientVersion(2)
        setRenderer(this)
        renderMode = RENDERMODE_CONTINUOUSLY
    }

    private val vs = """
        attribute vec2 aPos; varying vec2 vUv;
        uniform vec4 uRect;   // x, y, w, h in clip space
        uniform vec3 uMove;   // whole-frame zoom and shake: scale, dx, dy (clip space)
        void main() { vUv = aPos; vec2 p = vec2(uRect.x + aPos.x * uRect.z, uRect.y - aPos.y * uRect.w); gl_Position = vec4(p * uMove.x + uMove.yz, 0.0, 1.0); }
    """.trimIndent()

    private val fs = """
        #extension GL_OES_EGL_image_external : require
        precision mediump float;
        varying vec2 vUv;
        uniform samplerExternalOES uTex; uniform mat4 uST;
        uniform vec4 uCrop; uniform vec2 uSize; uniform float uShape; uniform float uRad; uniform float uBorder;
        uniform vec4 uBColor; uniform float uAlpha; uniform float uBlur; uniform float uDim;
        uniform vec4 uCol;    // r, g, b multipliers and saturation
        uniform vec4 uTone;   // contrast, brightness, rgb shift (px), noise
        uniform float uTime;
        vec4 tap(vec2 uv) { vec2 s = uCrop.xy + uv * uCrop.zw; return texture2D(uTex, (uST * vec4(s.x, 1.0 - s.y, 0.0, 1.0)).xy); }
        void main() {
            vec2 p = vUv * uSize; vec2 hs = uSize * 0.5; float d;
            if (uShape > 1.5) { vec2 q = (p - hs) / hs; d = (length(q) - 1.0) * min(hs.x, hs.y); }
            else { float r = uShape > 0.5 ? uRad : 0.0; vec2 q = abs(p - hs) - (hs - vec2(r)); d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r; }
            if (d > 0.5) discard;
            vec4 c;
            if (uBlur > 0.0) {
                vec2 o = vec2(uBlur) / uSize; c = vec4(0.0);
                for (int i = -2; i <= 2; i++) for (int j = -2; j <= 2; j++) c += tap(clamp(vUv + vec2(float(i), float(j)) * o, 0.0, 1.0));
                c /= 25.0;
            } else if (uTone.z > 0.0) {
                vec2 sh = vec2(uTone.z, uTone.z / 3.0) / uSize;
                c = tap(vUv); c.r = tap(clamp(vUv - vec2(sh.x, 0.0), 0.0, 1.0)).r; c.b = tap(clamp(vUv + vec2(sh.x, 0.0), 0.0, 1.0)).b; c.g = tap(clamp(vUv + vec2(0.0, sh.y), 0.0, 1.0)).g;
            } else c = tap(vUv);
            c.rgb *= (1.0 - uDim);
            float l = dot(c.rgb, vec3(0.299, 0.587, 0.114));
            c.rgb = mix(vec3(l), c.rgb, uCol.w) * uCol.rgb;
            c.rgb = (c.rgb - 0.5) * uTone.x + 0.5 + uTone.y;
            if (uTone.w > 0.0) c.rgb += (fract(sin(dot(gl_FragCoord.xy + uTime * 91.7, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) * uTone.w * 2.0;
            c.rgb = clamp(c.rgb, 0.0, 1.0);
            if (uBorder > 0.0 && d > -uBorder) c = uBColor;
            float edge = clamp(0.5 - d, 0.0, 1.0);
            gl_FragColor = vec4(c.rgb, uAlpha * edge);
        }
    """.trimIndent()

    private fun shader(type: Int, src: String) = GLES20.glCreateShader(type).also { GLES20.glShaderSource(it, src); GLES20.glCompileShader(it) }

    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        val t = IntArray(1); GLES20.glGenTextures(1, t, 0); tex = t[0]
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, tex)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
        prog = GLES20.glCreateProgram().also {
            GLES20.glAttachShader(it, shader(GLES20.GL_VERTEX_SHADER, vs)); GLES20.glAttachShader(it, shader(GLES20.GL_FRAGMENT_SHADER, fs)); GLES20.glLinkProgram(it)
        }
        val v = floatArrayOf(0f, 0f, 1f, 0f, 0f, 1f, 1f, 1f)
        vb = ByteBuffer.allocateDirect(v.size * 4).order(ByteOrder.nativeOrder()).asFloatBuffer().apply { put(v); position(0) }
        val s = SurfaceTexture(tex); st = s
        val surface = Surface(s)
        post { onSurface(surface) }
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) { vw = width; vh = height; GLES20.glViewport(0, 0, width, height) }

    override fun onDrawFrame(gl: GL10?) {
        val f = frame
        st?.let { runCatching { it.updateTexImage(); it.getTransformMatrix(stm) } }
        GLES20.glClearColor(((f.bg shr 16) and 255) / 255f, ((f.bg shr 8) and 255) / 255f, (f.bg and 255) / 255f, 1f)
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT)
        GLES20.glEnable(GLES20.GL_BLEND); GLES20.glBlendFunc(GLES20.GL_SRC_ALPHA, GLES20.GL_ONE_MINUS_SRC_ALPHA)
        GLES20.glUseProgram(prog)
        val aPos = GLES20.glGetAttribLocation(prog, "aPos")
        GLES20.glEnableVertexAttribArray(aPos); GLES20.glVertexAttribPointer(aPos, 2, GLES20.GL_FLOAT, false, 0, vb)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0); GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, tex)
        GLES20.glUniform1i(GLES20.glGetUniformLocation(prog, "uTex"), 0)
        GLES20.glUniformMatrix4fv(GLES20.glGetUniformLocation(prog, "uST"), 1, false, stm, 0)
        val k = vw / W
        val fx = f.fx
        GLES20.glUniform3f(GLES20.glGetUniformLocation(prog, "uMove"), fx.scale, fx.dx / W * 2 * fx.scale, -fx.dy / H * 2 * fx.scale)
        GLES20.glUniform4f(GLES20.glGetUniformLocation(prog, "uCol"), fx.r, fx.g, fx.b, fx.sat)
        GLES20.glUniform4f(GLES20.glGetUniformLocation(prog, "uTone"), fx.contrast, fx.bright, fx.shift * k, fx.noise)
        GLES20.glUniform1f(GLES20.glGetUniformLocation(prog, "uTime"), f.time)
        for (q in f.quads) {
            GLES20.glUniform4f(GLES20.glGetUniformLocation(prog, "uRect"), q.x / W * 2 - 1, 1 - q.y / H * 2, q.w / W * 2, q.h / H * 2)
            GLES20.glUniform4f(GLES20.glGetUniformLocation(prog, "uCrop"), q.cx, q.cy, q.cw, q.ch)
            GLES20.glUniform2f(GLES20.glGetUniformLocation(prog, "uSize"), q.w * k, q.h * k)
            GLES20.glUniform1f(GLES20.glGetUniformLocation(prog, "uShape"), q.shape.toFloat())
            GLES20.glUniform1f(GLES20.glGetUniformLocation(prog, "uRad"), q.radius * k)
            GLES20.glUniform1f(GLES20.glGetUniformLocation(prog, "uBorder"), q.border * k)
            GLES20.glUniform4f(GLES20.glGetUniformLocation(prog, "uBColor"), ((q.bcolor shr 16) and 255) / 255f, ((q.bcolor shr 8) and 255) / 255f, (q.bcolor and 255) / 255f, 1f)
            GLES20.glUniform1f(GLES20.glGetUniformLocation(prog, "uAlpha"), q.alpha)
            GLES20.glUniform1f(GLES20.glGetUniformLocation(prog, "uBlur"), q.blur * k)
            GLES20.glUniform1f(GLES20.glGetUniformLocation(prog, "uDim"), q.dim)
            GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
        }
    }

    fun release() { st?.release(); st = null }
}
