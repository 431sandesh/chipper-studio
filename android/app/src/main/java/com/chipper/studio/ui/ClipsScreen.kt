package com.chipper.studio.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.chipper.studio.AppModel
import com.chipper.studio.net.objects
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.net.URLEncoder

fun enc(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")

/** Cover image of a clip, loaded with the login cookie. */
@Composable
fun coverRequest(model: AppModel, c: JSONObject, bust: Any? = null): ImageRequest {
    val ctx = LocalContext.current
    val name = c.optString("name")
    return ImageRequest.Builder(ctx)
        .data(model.api.url("/cover/${enc(name)}__cover.jpg?${bust ?: model.coverV[name] ?: c.optDouble("rendered", 0.0)}"))
        .addHeader("Cookie", model.api.cookieHeader())
        .error(android.R.color.black)
        .crossfade(true).build()
}

fun postedTo(c: JSONObject): Int = c.optJSONObject("posts")?.let { p -> p.keys().asSequence().count { p.optJSONObject(it)?.optString("status") == "posted" } } ?: 0

enum class Filter(val label: String) { ALL("All"), READY("Ready"), POSTED("Posted"), MAKING("Making") }

@Composable
fun ClipsScreen(model: AppModel, onOpen: (JSONObject) -> Unit) {
    var clips by remember { mutableStateOf(model.cached("/api/clips")?.optJSONArray("clips")?.objects()) }
    var filter by remember { mutableStateOf(Filter.ALL) }
    var err by remember { mutableStateOf("") }
    LaunchedEffect(Unit) {
        while (true) {
            runCatching { model.load("/api/clips") }
                .onSuccess { clips = it.getJSONArray("clips").objects(); err = "" }
                .onFailure { err = it.message ?: "" }
            delay(5000)
        }
    }
    val shown = (clips ?: emptyList()).filter {
        when (filter) {
            Filter.ALL -> true
            Filter.READY -> it.optString("status") == "done" && postedTo(it) == 0
            Filter.POSTED -> postedTo(it) > 0
            Filter.MAKING -> it.optString("status") != "done" && it.optString("status") != "error"
        }
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Filter.entries.forEach { f -> FilterChip(filter == f, { filter = f }, label = { Text(f.label) }) }
        }
        if (err.isNotEmpty() && clips == null) Text(err, Modifier.padding(16.dp), color = MaterialTheme.colorScheme.error)
        if (clips == null) GridSkeleton()
        LazyVerticalGrid(GridCells.Adaptive(160.dp), contentPadding = PaddingValues(12.dp), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(shown, key = { it.optString("name") }) { c -> ClipCard(model, c) { onOpen(c) } }
        }
    }
}

@Composable
private fun ClipCard(model: AppModel, c: JSONObject, onClick: () -> Unit) {
    val status = c.optString("status")
    val posted = postedTo(c)
    Box(Modifier.fillMaxWidth().aspectRatio(9f / 16f).clip(RoundedCornerShape(16.dp)).background(Color.Black).clickable(onClick = onClick)) {
        AsyncImage(coverRequest(model, c), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        Box(Modifier.fillMaxSize().background(Brush.verticalGradient(0.55f to Color.Transparent, 1f to Color.Black.copy(alpha = 0.85f))))
        Row(Modifier.align(Alignment.TopStart).padding(8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            val (label, col) = when {
                status == "error" -> "Failed" to Bad
                status != "done" -> status.replaceFirstChar { it.uppercase() }.ifEmpty { "Making" } to Warn
                posted > 0 -> "Posted ×$posted" to Good
                else -> "Ready" to Violet
            }
            Text(label, Modifier.clip(RoundedCornerShape(8.dp)).background(col).padding(horizontal = 8.dp, vertical = 3.dp), color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.Bold)
            if (!c.isNull("score") && c.optDouble("score", -1.0) >= 0) Text("${c.optDouble("score").toInt()}", Modifier.clip(RoundedCornerShape(8.dp)).background(Color.Black.copy(alpha = 0.6f)).padding(horizontal = 7.dp, vertical = 3.dp), color = Color.White, fontSize = 11.sp)
        }
        Column(Modifier.align(Alignment.BottomStart).padding(10.dp)) {
            Text(c.optString("yt_title").ifEmpty { c.optString("hook") }.ifEmpty { c.optString("name") }, color = Color.White, fontWeight = FontWeight.SemiBold, fontSize = 13.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
            Text("${c.optDouble("duration", 0.0).toInt()} s · ${c.optString("channel").ifEmpty { c.optString("source") }}", color = Color.White.copy(alpha = 0.75f), fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}
