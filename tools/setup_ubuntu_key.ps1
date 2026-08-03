[CmdletBinding()]
param(
    [string]$UbuntuHost = "192.168.253.128",
    [string]$UbuntuUser = "panwenhui",
    [string]$KeyPath = "$HOME\.ssh\robogame_ubuntu"
)

$ErrorActionPreference = "Stop"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Command '$Name' was not found. Install Windows OpenSSH Client first."
    }
}

Require-Command "ssh"
Require-Command "ssh-keygen"

$keyDirectory = Split-Path -Parent $KeyPath
New-Item -ItemType Directory -Force -Path $keyDirectory | Out-Null

if (-not (Test-Path -LiteralPath $KeyPath)) {
    Write-Host "Creating SSH key: $KeyPath"
    Write-Host "When asked for a passphrase, press Enter twice for password-free syncing."
    & ssh-keygen -t ed25519 -f $KeyPath
    if ($LASTEXITCODE -ne 0) {
        throw "ssh-keygen failed with exit code $LASTEXITCODE"
    }
}
else {
    Write-Host "Using existing SSH key: $KeyPath"
}

$publicKey = "$KeyPath.pub"
if (-not (Test-Path -LiteralPath $publicKey)) {
    throw "Public key not found: $publicKey"
}

Write-Host "Copying the public key to ${UbuntuUser}@${UbuntuHost}."
Write-Host "Enter the Ubuntu login password when prompted. Password input is not displayed."

Get-Content -LiteralPath $publicKey | & ssh "${UbuntuUser}@${UbuntuHost}" `
    "umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys"

if ($LASTEXITCODE -ne 0) {
    throw "Could not copy the SSH key. Check the VM IP, username, password, and SSH service."
}

Write-Host "Testing password-free login..."
& ssh -i $KeyPath -o BatchMode=yes -o ConnectTimeout=10 "${UbuntuUser}@${UbuntuHost}" `
    "printf 'SSH key login OK\n'"

if ($LASTEXITCODE -ne 0) {
    throw "Key login test failed. Run this script again or inspect ~/.ssh/authorized_keys on Ubuntu."
}

Write-Host "Setup complete. Future syncs should not ask for the Ubuntu password."
