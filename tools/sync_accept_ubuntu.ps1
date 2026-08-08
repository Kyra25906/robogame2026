[CmdletBinding()]
param(
    [string]$UbuntuHost = "192.168.253.128",
    [string]$UbuntuUser = "panwenhui",
    [string]$RepoUrl = "https://github.com/Kyra25906/robogame2026.git",
    [string]$RemoteBase = "/home/panwenhui/robogame_acceptance",
    [string]$Branch = "codex/hardware-readiness",
    [string]$RosDistro = "jazzy",
    [string]$KeyPath = "$HOME\.ssh\robogame_ubuntu",
    [switch]$SkipTests,
    [switch]$SkipSmoke,
    [switch]$SkipMechanism,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Command '$Name' was not found."
    }
}

foreach ($value in @($UbuntuHost, $UbuntuUser, $RepoUrl, $RemoteBase, $Branch, $RosDistro)) {
    if ($value -notmatch '^[A-Za-z0-9_.:/-]+$') {
        throw "Unsafe character found in an argument: $value"
    }
}

Require-Command "git"
Push-Location $projectRoot
try {
    $commit = (& git rev-parse HEAD).Trim()
    $shortCommit = (& git rev-parse --short HEAD).Trim()
    & git diff --quiet HEAD --
    if ($LASTEXITCODE -ne 0) {
        throw "Tracked files have uncommitted changes. Commit them before acceptance."
    }
    & git merge-base --is-ancestor $commit "origin/$Branch"
    if ($LASTEXITCODE -ne 0) {
        throw "HEAD $shortCommit is not on origin/$Branch. Push it first."
    }
}
finally {
    Pop-Location
}

$acceptanceDir = "${RemoteBase}_${shortCommit}"
$remoteArgs = @(
    $RepoUrl,
    $acceptanceDir,
    $Branch,
    $commit,
    $shortCommit,
    $RosDistro,
    $(if ($SkipTests) { "1" } else { "0" }),
    $(if ($SkipSmoke) { "1" } else { "0" }),
    $(if ($SkipMechanism) { "1" } else { "0" })
)

Write-Host "Commit:          $shortCommit"
Write-Host "Git source:      $RepoUrl"
Write-Host "Acceptance copy: $acceptanceDir"
Write-Host "Branch:          $Branch"

if ($PlanOnly) {
    Write-Host "PlanOnly selected; no SSH connection was made."
    exit 0
}

Require-Command "ssh"
$sshOptions = @("-o", "ConnectTimeout=10")
if (Test-Path -LiteralPath $KeyPath) {
    $sshOptions += @("-i", $KeyPath)
}
else {
    Write-Warning "SSH key not found at $KeyPath; Ubuntu may ask for a password."
}

$remoteScript = @'
set -euo pipefail

repo_url="$1"
acceptance_dir="$2"
branch="$3"
commit="$4"
short_commit="$5"
ros_distro="$6"
skip_tests="$7"
skip_smoke="$8"
skip_mechanism="$9"

echo "[1/6] Preparing an independent clean repository"
if [ -e "$acceptance_dir" ]; then
    test -d "$acceptance_dir/.git"
    actual_commit="$(git -C "$acceptance_dir" rev-parse HEAD)"
    if [ "$actual_commit" != "$commit" ]; then
        echo "ERROR: $acceptance_dir exists at another commit: $actual_commit" >&2
        exit 20
    fi
    echo "Reusing $acceptance_dir"
else
    git clone --branch "$branch" --single-branch "$repo_url" "$acceptance_dir"
    git -C "$acceptance_dir" checkout --detach "$commit"
fi

actual_commit="$(git -C "$acceptance_dir" rev-parse HEAD)"
test "$actual_commit" = "$commit"
echo "Verified commit: $(git -C "$acceptance_dir" rev-parse --short HEAD)"

echo "[2/6] Verified clean source; building ROS2 workspace"
cd "$acceptance_dir/ros2_ws"
source "/opt/ros/$ros_distro/setup.bash"
colcon build --symlink-install
source install/setup.bash

if [ "$skip_tests" = "0" ]; then
    echo "[3/6] Running unit tests"
    cd "$acceptance_dir"
    python3 -m unittest discover -s tests -p 'test_*.py'
else
    echo "[3/6] Unit tests skipped"
fi

if [ "$skip_smoke" = "0" ]; then
    echo "[4/6] Running mock closed-loop smoke"
    cd "$acceptance_dir/ros2_ws"
    ros2 launch robogame_bringup manipulator_mock_smoke.launch.py
else
    echo "[4/6] Mock smoke skipped"
fi

if [ "$skip_mechanism" = "0" ]; then
    echo "[5/6] Running mechanism one-line acceptance"
    cd "$acceptance_dir"
    mkdir -p acceptance_results
    stamp="$(date +%Y%m%d_%H%M%S)"
    result_csv="acceptance_results/mechanism_${short_commit}_${stamp}.csv"
    bridge_log="acceptance_results/robot_bridge_${short_commit}_${stamp}.log"

    ros2 run robot_bridge robot_bridge --ros-args \
        -p mock_mode:=true -p mock_start_after_s:=0.2 \
        >"$bridge_log" 2>&1 &
    bridge_pid=$!
    cleanup_bridge() {
        kill -INT "$bridge_pid" 2>/dev/null || true
        wait "$bridge_pid" 2>/dev/null || true
    }
    trap cleanup_bridge EXIT

    service_ready=0
    for _ in $(seq 1 50); do
        if ros2 service list | grep -qx '/gripper/grab'; then
            service_ready=1
            break
        fi
        sleep 0.1
    done
    if [ "$service_ready" != "1" ]; then
        echo "ERROR: robot_bridge service did not become ready" >&2
        exit 21
    fi

    python3 tools/mechanism_acceptance.py \
        --all --repeat 1 --output "$result_csv"
    cleanup_bridge
    trap - EXIT

    echo "CSV: $acceptance_dir/$result_csv"
    wc -l "$result_csv"
    tail -n 2 "$result_csv"
else
    echo "[5/6] Mechanism acceptance skipped"
fi

echo "[6/6] All selected checks completed"
echo "ACCEPTANCE PASS: commit $short_commit"
'@

Write-Host "Connecting to ${UbuntuUser}@${UbuntuHost}..."
$remoteScript | ssh @sshOptions "${UbuntuUser}@${UbuntuHost}" bash -s -- @remoteArgs
if ($LASTEXITCODE -ne 0) {
    throw "Ubuntu synchronization or acceptance failed with exit code $LASTEXITCODE."
}

Write-Host "Sync and acceptance completed for commit $shortCommit."
Write-Host "Ubuntu result directory: $acceptanceDir/acceptance_results"
