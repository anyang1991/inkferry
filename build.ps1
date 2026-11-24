param(
    [string]$Python = "python",
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "dist")
)

$ErrorActionPreference = "Stop"
$buildDirectory = Join-Path $PSScriptRoot ".build"
$binaryDirectory = Join-Path $buildDirectory "portable"
$pythonHome = & $Python -c "import sys; print(sys.base_prefix)"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
$pythonLicense = Join-Path $pythonHome "LICENSE.txt"
if (!(Test-Path -LiteralPath $pythonLicense -PathType Leaf)) {
    throw "Python LICENSE.txt is missing from $pythonHome"
}
$assets = "index.html", "style.css", "app.js", "kindle.css", "kindle.js", "favicon.svg"
$options = @(
    "--onefile", "--console", "--noconfirm", "--clean", "--name", "InkFerry",
    "--distpath", $binaryDirectory, "--workpath", $buildDirectory,
    "--specpath", $buildDirectory
)
foreach ($asset in $assets) {
    $options += "--add-data"
    $options += "$(Join-Path $PSScriptRoot $asset):."
}
& $Python -m PyInstaller @options (Join-Path $PSScriptRoot "server.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$licenseCopy = Join-Path $binaryDirectory "LICENSE-Python.txt"
Copy-Item -LiteralPath $pythonLicense -Destination $licenseCopy
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$bundle = Join-Path $OutputDirectory "InkFerry-Windows.zip"
Compress-Archive -LiteralPath @(
    (Join-Path $binaryDirectory "InkFerry.exe"),
    (Join-Path $PSScriptRoot "README.md"),
    $licenseCopy
) -DestinationPath $bundle -Force
Write-Output $bundle
