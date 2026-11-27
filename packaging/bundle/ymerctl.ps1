# Manage a Ymer server bundle (Docker Compose) on Windows. Run: .\ymerctl.ps1 help
param(
  [Parameter(Position = 0)] [string] $Command = "help",
  [Parameter(Position = 1, ValueFromRemainingArguments = $true)] [string[]] $Rest
)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ComposeFile = Join-Path $Here "compose.yaml"
$EnvFile = if ($env:YMER_ENV_FILE) { $env:YMER_ENV_FILE } else { Join-Path $Here "ymer.env" }
$env:YMER_ENV_FILE = $EnvFile

function Show-Usage {
  @"
Usage: ymerctl.ps1 <command>

  init      Create the settings file with fresh secrets (kept if it exists)
  start     Start Ymer (pulls images the first time) and wait until it is ready
  stop      Stop Ymer; data is kept
  restart   Stop, then start
  status    Show the services and their health
  logs      Follow the logs (ymerctl.ps1 logs api, for one service)
  update    Pull the images for this version, then restart
  open      Open Ymer in the browser
  launch    Start Ymer, then open it (what the Start menu shortcut runs)
  url       Print the address Ymer is served on

Settings: $EnvFile
"@
}

function Fail([string] $Message) {
  Write-Host "ymerctl: $Message" -ForegroundColor Red
  if ($Command -eq "launch") { Read-Host "Press Enter to close" }
  exit 1
}

function Invoke-Compose {
  & docker compose --project-directory $Here -f $ComposeFile --env-file $EnvFile @args
  if ($LASTEXITCODE -ne 0) { Fail "docker compose $($args -join ' ') failed." }
}

function Test-Docker {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker Desktop is not installed. Get it from https://www.docker.com/products/docker-desktop/"
  }
  & docker info *> $null
  if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop is not running. Start it, wait until it says it is running, then try again." }
}

function New-Secret {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}

function Get-Setting([string] $Name) {
  if (-not (Test-Path $EnvFile)) { return $null }
  $line = Get-Content $EnvFile | Where-Object { $_ -match "^$Name=" } | Select-Object -Last 1
  if ($line) { return $line.Substring($Name.Length + 1) }
  return $null
}

function Initialize-Settings {
  if (Test-Path $EnvFile) { return }
  $dir = Split-Path -Parent $EnvFile
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
  $port = if ($env:YMER_HTTP_PORT) { $env:YMER_HTTP_PORT } else { "8080" }
  $bind = if ($env:YMER_HTTP_BIND) { $env:YMER_HTTP_BIND } else { "127.0.0.1" }
  $url = if ($env:YMER_PUBLIC_URL) { $env:YMER_PUBLIC_URL } else { "http://localhost:$port" }
  $secure = if ($url.StartsWith("https://")) { "true" } else { "false" }
  $text = @"
# Ymer settings. Restart after changing them: Stop Ymer, then Start Ymer.
# Keep this file private: it holds the secrets below.

# Where people open Ymer. File downloads are signed for this address.
YMER_PUBLIC_URL=$url
# 0.0.0.0 makes Ymer reachable from other machines on your network.
YMER_HTTP_BIND=$bind
YMER_HTTP_PORT=$port
YMER_COOKIE_SECURE=$secure

# Generated secrets. Leave the database and storage passwords as they are.
POSTGRES_PASSWORD=$(New-Secret)
MINIO_ROOT_PASSWORD=$(New-Secret)
YMER_SECRET_KEY=$(New-Secret)
YMER_COLLAB_SECRET=$(New-Secret)

# AI: fake = offline demo, anthropic = Claude, openai = any OpenAI-compatible server, off.
YMER_AI_PROVIDER=fake
# ANTHROPIC_API_KEY=
# YMER_AI_BASE_URL=http://host.docker.internal:11434/v1
YMER_AI_EMBEDDING_PROVIDER=fake

# Factory builds: fake, modal (set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET) or off.
YMER_FACTORY_SANDBOX=fake
"@
  # UTF-8 without a byte order mark, which Docker Compose reads correctly.
  [System.IO.File]::WriteAllText($EnvFile, $text.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding $false))
  Write-Host "Created $EnvFile"
}

function Get-Url {
  $u = Get-Setting "YMER_PUBLIC_URL"
  if ($u) { return $u }
  return "http://localhost:8080"
}

function Start-Ymer {
  Test-Docker
  Initialize-Settings
  Write-Host "Starting Ymer (the first start downloads about 1 GB of images)..."
  Invoke-Compose up -d --wait --wait-timeout 600 web
  Invoke-Compose up -d ai-worker factory-worker
  Write-Host "Ymer is running at $(Get-Url)" -ForegroundColor Green
}

switch ($Command) {
  "init" { Initialize-Settings }
  { $_ -in "start", "up" } { Start-Ymer }
  { $_ -in "stop", "down" } { Test-Docker; Invoke-Compose stop }
  "restart" { Test-Docker; Invoke-Compose stop; Start-Ymer }
  { $_ -in "status", "ps" } { Test-Docker; Invoke-Compose ps --all }
  "logs" { Test-Docker; Invoke-Compose logs --follow --tail 200 @Rest }
  "update" { Test-Docker; Initialize-Settings; Invoke-Compose pull; Start-Ymer }
  "open" { Start-Process (Get-Url) }
  "launch" {
    try { Start-Ymer; Start-Process (Get-Url); Start-Sleep -Seconds 3 }
    catch { Write-Host $_ -ForegroundColor Red; Read-Host "Press Enter to close" }
  }
  "url" { Get-Url }
  "compose" { Test-Docker; Invoke-Compose @Rest }
  default { Show-Usage }
}
