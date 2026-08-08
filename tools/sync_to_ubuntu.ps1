[CmdletBinding()]
param(
    [string]$UbuntuHost = "192.168.253.128",
    [string]$UbuntuUser = "panwenhui",
    [string]$RemoteProject = "/home/panwenhui/robogame",
    [string]$RosDistro = "jazzy",
    [string]$KeyPath = "$HOME\.ssh\robogame_ubuntu",
    [switch]$SkipBuild,
    [switch]$SkipTests,
    [switch]$KeepArchive,
    [switch]$PackageOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputDirectory = Join-Path $projectRoot "outputs\sync"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$archive = Join-Path $outputDirectory "robogame_sync_$stamp.tar.gz"
$remoteArchive = "/tmp/robogame_sync_$stamp.tar.gz"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Command '$Name' was not found. Install Windows OpenSSH Client first."
    }
}

foreach ($value in @($UbuntuHost, $UbuntuUser, $RemoteProject, $RosDistro)) {
    if ($value -notmatch '^[A-Za-z0-9_.:/-]+$') {
        throw "Unsafe character found in a connection or remote-path parameter: $value"
    }
}

Require-Command "tar.exe"
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

Write-Host "[1/4] Packaging source files..."
$tarArguments = @(
    "-czf", $archive,
    "--exclude=__pycache__",
    "--exclude=*.pyc",
    "ros2_ws/src",
    "tests",
    "docs",
    "tools",
    "README.md"
)

Push-Location $projectRoot
try {
    & tar.exe @tarArguments
    if ($LASTEXITCODE -ne 0) {
        throw "tar.exe failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host "Archive created: $archive"

if ($PackageOnly) {
    Write-Host "PackageOnly selected; no network connection was made."
    exit 0
}

Require-Command "scp"
Require-Command "ssh"

$sshOptions = @("-o", "ConnectTimeout=10")
if (Test-Path -LiteralPath $KeyPath) {
    $sshOptions += @("-i", $KeyPath)
}
else {
    Write-Warning "SSH key not found at $KeyPath. You may be prompted for the Ubuntu password twice."
    Write-Warning "Run tools\setup_ubuntu_key.ps1 once to enable password-free syncing."
}

Write-Host "[2/4] Uploading to ${UbuntuUser}@${UbuntuHost}..."
& scp @sshOptions $archive "${UbuntuUser}@${UbuntuHost}:$remoteArchive"
if ($LASTEXITCODE -ne 0) {
    throw "scp failed with exit code $LASTEXITCODE"
}

$remoteSteps = @(
    "set -e",
    "mkdir -p '$RemoteProject'",
    "tar -xzf '$remoteArchive' -C '$RemoteProject'",
    "rm -f '$remoteArchive'"
)

if (-not $SkipBuild) {
    $remoteSteps += @(
        "cd '$RemoteProject/ros2_ws'",
        "source '/opt/ros/$RosDistro/setup.bash'",
        "colcon build --symlink-install"
    )
}

if (-not $SkipTests) {
    $remoteSteps += @(
        "cd '$RemoteProject'",
        "source '/opt/ros/$RosDistro/setup.bash'",
        "if [ -f '$RemoteProject/ros2_ws/install/setup.bash' ]; then source '$RemoteProject/ros2_ws/install/setup.bash'; fi",
        "python3 -m unittest discover -s tests -v"
    )
}

$remoteCommand = $remoteSteps -join "; "
Write-Host "[3/4] Applying update on Ubuntu..."
& ssh @sshOptions "${UbuntuUser}@${UbuntuHost}" $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "Remote update, build, or test failed with exit code $LASTEXITCODE. Files may already be synchronized; read the error above."
}

Write-Host "[4/4] Sync complete."
if (-not $KeepArchive) {
    Remove-Item -LiteralPath $archive -Force
}
else {
    Write-Host "Local archive kept at: $archive"
}
