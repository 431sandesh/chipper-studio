package com.chipper.studio.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// Chipper's own colours: hot pink accent, violet second accent, soft lavender-grey panels
val Pink = Color(0xFFFF3D6E)
val PinkDeep = Color(0xFFE0245A)
val Violet = Color(0xFF7C5CFF)
val Mint = Color(0xFF2EE6C8)
val Good = Color(0xFF1DB954)
val Warn = Color(0xFFFFB020)
val Bad = Color(0xFFE5484D)

private val Light = lightColorScheme(
    primary = Pink, onPrimary = Color.White,
    secondary = Violet, onSecondary = Color.White,
    tertiary = Mint,
    background = Color(0xFFF6F6FA), onBackground = Color(0xFF15161C),
    surface = Color.White, onSurface = Color(0xFF15161C),
    surfaceVariant = Color(0xFFEEEEF5), onSurfaceVariant = Color(0xFF5E6274),
    outline = Color(0xFFD9DAE5), error = Bad,
)
private val Dark = darkColorScheme(
    primary = Pink, onPrimary = Color.White,
    secondary = Violet, onSecondary = Color.White,
    tertiary = Mint,
    background = Color(0xFF101117), onBackground = Color(0xFFEDEEF3),
    surface = Color(0xFF181A22), onSurface = Color(0xFFEDEEF3),
    surfaceVariant = Color(0xFF22252F), onSurfaceVariant = Color(0xFFA2A6B7),
    outline = Color(0xFF30333F), error = Bad,
)

@Composable
fun ChipperTheme(dark: Boolean = false, content: @Composable () -> Unit) {
    val base = Typography()
    val type = base.copy(
        titleLarge = base.titleLarge.copy(fontWeight = FontWeight.Bold, fontSize = 22.sp),
        titleMedium = base.titleMedium.copy(fontWeight = FontWeight.SemiBold),
        labelLarge = base.labelLarge.copy(fontWeight = FontWeight.SemiBold),
    )
    MaterialTheme(colorScheme = if (dark) Dark else Light, typography = type, content = content)
}

val Mono = TextStyle(fontSize = 12.sp)
