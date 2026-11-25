package com.chipper.studio.ui

import android.annotation.SuppressLint
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.viewinterop.AndroidView
import com.chipper.studio.AppModel

/**
 * The editor and thumbnail editor, for now shown from the laptop's own editor page inside the app (no browser).
 * The native editor replaces this in part 2.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun WebEditor(model: AppModel, name: String, cover: Boolean = false, onClose: () -> Unit) {
    var pick by remember { mutableStateOf<ValueCallback<Array<Uri>>?>(null) }
    val chooser = rememberLauncherForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        pick?.onReceiveValue(uris.toTypedArray()); pick = null
    }
    val base = model.api.base
    remember(base) {
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setCookie(base, "${model.api.cookieHeader()}; Path=/; Secure")
            flush()
        }
    }
    AndroidView(
        factory = { ctx ->
            WebView(ctx).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                settings.mediaPlaybackRequiresUserGesture = false
                settings.allowFileAccess = false
                setBackgroundColor(android.graphics.Color.BLACK)
                addJavascriptInterface(object {
                    @JavascriptInterface fun close() { Handler(Looper.getMainLooper()).post { onClose() } }
                }, "ChipperApp")
                webViewClient = WebViewClient()
                webChromeClient = object : WebChromeClient() {
                    override fun onShowFileChooser(w: WebView?, cb: ValueCallback<Array<Uri>>?, p: FileChooserParams?): Boolean {
                        pick?.onReceiveValue(null); pick = cb
                        val accept = p?.acceptTypes?.firstOrNull { it.isNotBlank() } ?: "*/*"
                        chooser.launch(if (accept.contains("/")) accept else "*/*")
                        return true
                    }
                }
                loadUrl("$base/#app-${if (cover) "cover" else "edit"}=${enc(name)}")
            }
        },
        modifier = Modifier.fillMaxSize().background(Color.Black).systemBarsPadding(),
        onRelease = { it.destroy() },
    )
}
