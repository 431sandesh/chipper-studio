package com.chipper.studio.net

import android.content.Context
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import com.chipper.studio.AppModel
import com.chipper.studio.ui.LocalToast
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File

/** The file's own name on the phone (e.g. "boom.mp3"). */
fun displayName(ctx: Context, uri: Uri): String {
    runCatching {
        ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0)?.let { if (it.isNotBlank()) return it }
        }
    }
    return uri.lastPathSegment?.substringAfterLast('/') ?: "file"
}

/** Length of a sound in seconds (0 if unknown). Long sounds are treated as music. */
fun audioSeconds(ctx: Context, uri: Uri): Double = runCatching {
    MediaMetadataRetriever().run {
        setDataSource(ctx, uri)
        val ms = extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
        release(); ms / 1000.0
    }
}.getOrDefault(0.0)

/** Sends a picked phone file to the laptop. [fields] are extra form fields, e.g. kind=fx. */
suspend fun Api.uploadUri(ctx: Context, uri: Uri, path: String = "/api/uploads", fields: Map<String, String> = emptyMap()): JSONObject =
    withContext(Dispatchers.IO) {
        val name = displayName(ctx, uri)
        val mime = ctx.contentResolver.getType(uri) ?: "application/octet-stream"
        val dir = File(ctx.cacheDir, "up").apply { mkdirs() }
        val tmp = File(dir, System.nanoTime().toString() + "_" + name.replace(Regex("[^A-Za-z0-9_.-]"), "_"))
        try {
            ctx.contentResolver.openInputStream(uri)?.use { i -> tmp.outputStream().use { o -> i.copyTo(o) } } ?: throw ApiError("Could not read that file", 0)
            val mb = MultipartBody.Builder().setType(MultipartBody.FORM)
            fields.forEach { (k, v) -> mb.addFormDataPart(k, v) }
            mb.addFormDataPart("file", name, tmp.asRequestBody(mime.toMediaType()))
            val up = http.newBuilder().writeTimeout(5, java.util.concurrent.TimeUnit.MINUTES).readTimeout(5, java.util.concurrent.TimeUnit.MINUTES).build()
            up.newCall(Request.Builder().url(url(path)).post(mb.build()).build()).execute().use { parseBody(it.code, it.body?.string() ?: "") }
        } finally { tmp.delete() }
    }

/**
 * A "pick files from the phone and send them to the laptop" action.
 * [accept] is the file type filter ("image/ *", "audio/ *" or "* / *"), [kindFor] decides the folder per file.
 */
@Composable
fun rememberUploader(
    app: AppModel, accept: String, path: String = "/api/uploads",
    kindFor: (Context, Uri) -> String? = { _, _ -> null },
    onDone: (List<JSONObject>) -> Unit,
): () -> Unit {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val toast = LocalToast.current
    val scope = rememberCoroutineScope()
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        if (uris.isEmpty()) return@rememberLauncherForActivityResult
        scope.launch {
            toast.show(if (uris.size == 1) "Uploading…" else "Uploading ${uris.size} files…")
            val done = mutableListOf<JSONObject>()
            for (u in uris) {
                val kind = kindFor(ctx, u)
                runCatching { app.api.uploadUri(ctx, u, path, if (kind != null) mapOf("kind" to kind) else emptyMap()) }
                    .onSuccess { done += it }.onFailure { toast.show("${displayName(ctx, u)}: ${it.message ?: "upload failed"}") }
            }
            if (done.isNotEmpty()) { toast.show(if (done.size == 1) "Uploaded. It's saved on your PC" else "Uploaded ${done.size} files. They're saved on your PC"); onDone(done) }
        }
    }
    return { launcher.launch(accept) }
}
