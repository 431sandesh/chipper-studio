package com.chipper.studio.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** A grey shape with a soft light sweeping across, shown where content is still loading. */
@Composable
fun Shimmer(modifier: Modifier, radius: Dp = 14.dp) {
    val tr = rememberInfiniteTransition(label = "sh")
    val x by tr.animateFloat(-400f, 1200f, infiniteRepeatable(tween(1100, easing = LinearEasing), RepeatMode.Restart), label = "x")
    val base = MaterialTheme.colorScheme.surfaceVariant
    val hi = MaterialTheme.colorScheme.surface
    Box(modifier.clip(RoundedCornerShape(radius)).background(Brush.linearGradient(listOf(base, hi, base), start = Offset(x, 0f), end = Offset(x + 400f, 200f))))
}

@Composable
fun CardSkeleton(lines: Int = 3) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(MaterialTheme.colorScheme.surface).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Shimmer(Modifier.width(160.dp).height(20.dp), 6.dp)
        repeat(lines) { Shimmer(Modifier.fillMaxWidth().height(14.dp), 6.dp) }
        Shimmer(Modifier.fillMaxWidth().height(44.dp), 12.dp)
    }
}

@Composable
fun GridSkeleton() {
    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        repeat(3) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Shimmer(Modifier.weight(1f).aspectRatio(9f / 16f), 16.dp)
                Shimmer(Modifier.weight(1f).aspectRatio(9f / 16f), 16.dp)
            }
        }
    }
}
