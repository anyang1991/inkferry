param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "dist"),
    [string]$Gradle,
    [string[]]$GradleArgs = @()
)

$ErrorActionPreference = "Stop"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$stage = Join-Path $tempRoot ("inkferry-android-" + [Guid]::NewGuid().ToString("N"))
$android = Join-Path $stage "android"
$app = Join-Path $android "app"
New-Item -ItemType Directory -Path $app -Force | Out-Null

try {
    $shared = "server.py", "book.py", "mobi.py", "index.html", "app.js",
        "style.css", "kindle.css", "kindle.js", "favicon.svg"
    foreach ($name in $shared) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $stage
    }
    $project = Join-Path $PSScriptRoot "android"
    foreach ($name in @("settings.gradle", "build.gradle", "gradle.properties")) {
        Copy-Item -LiteralPath (Join-Path $project $name) -Destination $android
    }
    Copy-Item -LiteralPath (Join-Path $project "app/build.gradle") -Destination $app
    Copy-Item -LiteralPath (Join-Path $project "app/src") -Destination $app -Recurse

    if (!$Gradle) {
        Copy-Item -LiteralPath (Join-Path $project "gradle") -Destination $android -Recurse
        Copy-Item -LiteralPath (Join-Path $project "gradlew.bat") -Destination $android
        $Gradle = Join-Path $android "gradlew.bat"
    }
    & $Gradle @GradleArgs --console=plain -p $android :app:assembleDebug
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    $apkDirectory = Join-Path $app "build/outputs/apk"
    $architectures = [ordered]@{ arm64 = "arm64-v8a"; emulator = "x86_64" }
    foreach ($flavor in $architectures.Keys) {
        $apk = Join-Path $apkDirectory "$flavor/debug/app-$flavor-debug.apk"
        $result = Join-Path $OutputDirectory "InkFerry-Android-$($architectures[$flavor]).apk"
        Copy-Item -LiteralPath $apk -Destination $result -Force
        Write-Output $result
    }
}
finally {
    $resolved = (Resolve-Path -LiteralPath $stage).Path
    if ($resolved -eq $stage -and $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
