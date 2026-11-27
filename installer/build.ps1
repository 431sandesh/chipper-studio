# Builds everything the Windows installer packs: a private Python with Chipper's packages, ffmpeg, cloudflared
# and Chipper itself, into build\app. Run on Windows (GitHub Actions does this for every release).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$build = Join-Path $root 'build'
$app = Join-Path $build 'app'
if (Test-Path $build) { Remove-Item $build -Recurse -Force }
New-Item -ItemType Directory -Force $app, "$app\bin" | Out-Null
$gh = @{ 'User-Agent' = 'chipper-build' }
if ($env:GITHUB_TOKEN) { $gh['Authorization'] = "Bearer $env:GITHUB_TOKEN" }

Write-Host '== Python 3.12 (a private copy, only for Chipper)'
Invoke-WebRequest 'https://www.nuget.org/api/v2/package/python/3.12.8' -OutFile "$build\python.zip"
Expand-Archive "$build\python.zip" "$build\python-pkg"
Move-Item "$build\python-pkg\tools" "$app\python"
$py = "$app\python\python.exe"
& $py -m ensurepip --upgrade
if ($LASTEXITCODE) { throw 'ensurepip failed' }
& $py -m pip install --no-warn-script-location --upgrade pip
& $py -m pip install --no-warn-script-location -r requirements.txt
if ($LASTEXITCODE) { throw 'pip install failed' }

Write-Host '== ffmpeg'
$rel = Invoke-RestMethod 'https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest' -Headers $gh
$asset = $rel.assets | Where-Object { $_.name -match '^ffmpeg-n7\.1-latest-win64-gpl-7\.1\.zip$' } | Select-Object -First 1
if (-not $asset) { $asset = $rel.assets | Where-Object { $_.name -match '^ffmpeg-n[\d.]+-latest-win64-gpl-[\d.]+\.zip$' } | Sort-Object name -Descending | Select-Object -First 1 }
if (-not $asset) { $asset = $rel.assets | Where-Object { $_.name -eq 'ffmpeg-master-latest-win64-gpl.zip' } | Select-Object -First 1 }
Write-Host "   $($asset.name)"
Invoke-WebRequest $asset.browser_download_url -OutFile "$build\ffmpeg.zip"
Expand-Archive "$build\ffmpeg.zip" "$build\ffmpeg"
Get-ChildItem "$build\ffmpeg" -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination "$app\bin"
Get-ChildItem "$build\ffmpeg" -Recurse -Filter LICENSE.txt | Select-Object -First 1 | Copy-Item -Destination "$app\bin\FFMPEG-LICENSE.txt"

Write-Host '== cloudflared'
Invoke-WebRequest 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile "$app\bin\cloudflared.exe"

Write-Host '== Chipper Studio'
Copy-Item *.py, requirements*.txt, README.md -Destination $app
Copy-Item static, fonts -Destination $app -Recurse
Copy-Item installer\chipper.ico -Destination $app
Set-Content "$app\VERSION" $env:CHIPPER_VERSION
foreach ($t in 'ffmpeg.exe', 'ffprobe.exe', 'cloudflared.exe') { if (-not (Test-Path "$app\bin\$t")) { throw "$t is missing" } }
& "$app\bin\ffmpeg.exe" -hide_banner -version | Select-Object -First 1
& $py -c "import flask, streamlink, yt_dlp, playwright, edge_tts; print('packages ok')"
if ($LASTEXITCODE) { throw 'package check failed' }
