package com.chipper.studio.net

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.FormBody
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/** Everything the app keeps between launches. */
class Prefs(ctx: Context) {
    private val p: SharedPreferences = ctx.getSharedPreferences("chipper", Context.MODE_PRIVATE)
    var base: String
        get() = p.getString("base", "") ?: ""
        set(v) = p.edit().putString("base", v.trimEnd('/')).apply()
    var manualBase: String
        get() = p.getString("manual", "") ?: ""
        set(v) = p.edit().putString("manual", v.trim().trimEnd('/')).apply()
    var ntfyTopic: String
        get() = p.getString("topic", "") ?: ""
        set(v) = p.edit().putString("topic", v.trim()).apply()
    var ntfyServer: String
        get() = p.getString("ntfy", "https://ntfy.sh") ?: "https://ntfy.sh"
        set(v) = p.edit().putString("ntfy", v.trim().trimEnd('/').ifEmpty { "https://ntfy.sh" }).apply()
    var cookie: String
        get() = p.getString("cookie", "") ?: ""
        set(v) = p.edit().putString("cookie", v).apply()
    var lastMsg: String
        get() = p.getString("last", "") ?: ""
        set(v) = p.edit().putString("last", v).apply()
    var listen: Boolean
        get() = p.getBoolean("listen", true)
        set(v) = p.edit().putBoolean("listen", v).apply()
    fun cached(path: String): JSONObject? = p.getString("c:$path", null)?.let { runCatching { JSONObject(it) }.getOrNull() }
    fun store(path: String, j: JSONObject) { val t = j.toString(); if (t.length < 400_000) p.edit().putString("c:$path", t).apply() }
    var dark: Boolean
        get() = p.getBoolean("dark", false)
        set(v) = p.edit().putBoolean("dark", v).apply()
}

class ApiError(msg: String, val code: Int = 0) : Exception(msg)

/**
 * Talks to Chipper Studio on the laptop. Login is the same remote password the website uses; the
 * login cookie is kept so the app stays signed in (it lasts 30 days, like on the website).
 */
class Api(val prefs: Prefs) {
    private val jar = object : CookieJar {
        override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
            cookies.firstOrNull { it.name == "chipper_auth" }?.let { prefs.cookie = it.value }
        }
        override fun loadForRequest(url: HttpUrl): List<Cookie> {
            val v = prefs.cookie
            if (v.isEmpty()) return emptyList()
            return listOf(Cookie.Builder().name("chipper_auth").value(v).hostOnlyDomain(url.host).path("/").build())
        }
    }

    val http: OkHttpClient = OkHttpClient.Builder()
        .cookieJar(jar)
        .followRedirects(false)
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    val quick: OkHttpClient = http.newBuilder().connectTimeout(6, TimeUnit.SECONDS).readTimeout(8, TimeUnit.SECONDS).build()

    val base get() = prefs.base

    fun url(path: String): String = base + (if (path.startsWith("/")) path else "/$path")

    /** The login cookie as a header, for the video player and WebView. */
    fun cookieHeader(): String = if (prefs.cookie.isEmpty()) "" else "chipper_auth=${prefs.cookie}"

    suspend fun alive(b: String): Boolean = withContext(Dispatchers.IO) {
        try {
            quick.newCall(Request.Builder().url("$b/healthz").build()).execute().use { it.isSuccessful && it.body?.string()?.trim() == "ok" }
        } catch (e: Exception) { false }
    }

    /** True when the saved cookie is accepted. */
    suspend fun signedIn(): Boolean = withContext(Dispatchers.IO) {
        if (prefs.cookie.isEmpty()) return@withContext false
        try {
            http.newCall(Request.Builder().url(url("/api/state")).build()).execute().use { it.code == 200 }
        } catch (e: Exception) { false }
    }

    suspend fun login(password: String): String? = withContext(Dispatchers.IO) {
        prefs.cookie = ""
        try {
            val body = FormBody.Builder().add("password", password).build()
            http.newCall(Request.Builder().url(url("/login")).post(body).build()).execute().use { r ->
                when {
                    r.code in 300..399 && prefs.cookie.isNotEmpty() -> null
                    r.code == 429 -> "Too many wrong tries. Locked for 15 minutes."
                    r.code == 401 -> "Wrong password."
                    else -> "Could not sign in (${r.code})."
                }
            }
        } catch (e: Exception) { "Could not reach your laptop: ${e.message}" }
    }

    fun parseBody(code: Int, text: String): JSONObject = parse(code, text)
    private fun parse(code: Int, text: String): JSONObject {
        if (code == 401) throw ApiError("Signed out. Connect again.", 401)
        val j = try { JSONObject(text) } catch (e: Exception) { throw ApiError("Unexpected answer from the laptop ($code)", code) }
        if (!j.optBoolean("ok", false)) throw ApiError(j.optString("error", "Something went wrong"), code)
        return j
    }

    suspend fun get(path: String): JSONObject = withContext(Dispatchers.IO) {
        http.newCall(Request.Builder().url(url(path)).build()).execute().use { parse(it.code, it.body?.string() ?: "") }
    }

    suspend fun post(path: String, body: JSONObject = JSONObject()): JSONObject = withContext(Dispatchers.IO) {
        val rb = body.toString().toRequestBody("application/json".toMediaType())
        http.newCall(Request.Builder().url(url(path)).post(rb).build()).execute().use { parse(it.code, it.body?.string() ?: "") }
    }

    suspend fun delete(path: String): JSONObject = withContext(Dispatchers.IO) {
        http.newCall(Request.Builder().url(url(path)).delete().build()).execute().use { parse(it.code, it.body?.string() ?: "") }
    }

    suspend fun upload(path: String, file: File, field: String = "file", mime: String = "application/octet-stream"): JSONObject =
        withContext(Dispatchers.IO) {
            val mb = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart(field, file.name, file.asRequestBody(mime.toMediaType())).build()
            http.newCall(Request.Builder().url(url(path)).post(mb).build()).execute().use { parse(it.code, it.body?.string() ?: "") }
        }

    /** Downloads a file from the laptop into [out]. */
    suspend fun download(path: String, out: File): File = withContext(Dispatchers.IO) {
        http.newCall(Request.Builder().url(url(path)).build()).execute().use { r ->
            if (!r.isSuccessful) throw ApiError("Download failed (${r.code})", r.code)
            out.parentFile?.mkdirs()
            out.outputStream().use { o -> r.body!!.byteStream().copyTo(o) }
        }
        out
    }
}

/**
 * Finds the laptop. Chipper sends its new remote link to your ntfy topic every time it starts,
 * so the newest "Chipper is online" message holds the current address.
 */
object Finder {
    suspend fun latestLink(api: Api): String? = withContext(Dispatchers.IO) {
        val topic = api.prefs.ntfyTopic
        if (topic.isEmpty()) return@withContext null
        val u = "${api.prefs.ntfyServer}/${topic}/json?poll=1&since=7d".toHttpUrlOrNull() ?: return@withContext null
        try {
            api.quick.newBuilder().readTimeout(15, TimeUnit.SECONDS).build()
                .newCall(Request.Builder().url(u).build()).execute().use { r ->
                    if (!r.isSuccessful) return@withContext null
                    var best: String? = null
                    r.body?.string()?.lineSequence()?.forEach { line ->
                        if (line.isBlank()) return@forEach
                        val j = try { JSONObject(line) } catch (e: Exception) { return@forEach }
                        if (j.optString("event") != "message") return@forEach
                        val click = j.optString("click", "")
                        val text = j.optString("message", "")
                        val link = when {
                            click.contains("trycloudflare.com") || click.startsWith("https://") && j.optString("title").contains("online", true) -> click
                            else -> Regex("https://[a-z0-9-]+\\.trycloudflare\\.com").find(text)?.value
                        }
                        if (!link.isNullOrEmpty()) best = link.trimEnd('/')   // messages come oldest first
                    }
                    best
                }
        } catch (e: Exception) { null }
    }
}

fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }
fun JSONArray.strings(): List<String> = (0 until length()).map { optString(it) }
