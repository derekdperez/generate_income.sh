<#
.SYNOPSIS
  Starts the full Nightmare v2 stack locally (Docker Compose) for development/debugging.

.DESCRIPTION
  Brings up Postgres, Redis, RabbitMQ, Command Center, Gatekeeper, Spider, Enum, and PortScan
  using deploy/docker-compose.yml. Requires Docker Desktop (Windows) with Compose V2, or
  docker-compose v1 on PATH.

.PARAMETER Action
  up    Build images and start containers detached (default).
  down  Stop and remove containers.
  logs  Follow all service logs (blocks until Ctrl+C).
  ps    Show container status.

.EXAMPLE
  .\deploy\run-local.ps1
  .\deploy\run-local.ps1 -Action logs
#>
param(
  [ValidateSet("up", "down", "logs", "ps")]
  [string] $Action = "up"
)

$ErrorActionPreference = "Stop"

$dockerExe = $null
if (Get-Command docker.exe -ErrorAction SilentlyContinue) {
  $dockerExe = (Get-Command docker.exe).Source
}
elseif (Get-Command docker -ErrorAction SilentlyContinue) {
  $dockerExe = (Get-Command docker).Source
}
else {
  Write-Error "Docker is not on PATH. Install Docker Desktop for Windows."
}

$ScriptRoot = $PSScriptRoot
if (-not $ScriptRoot) { $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }

$ComposeFile = Join-Path $ScriptRoot "docker-compose.yml"
$Root = (Resolve-Path (Join-Path $ScriptRoot "..")).Path
Set-Location $Root

function Invoke-NightmareCompose {
  param(
    [Parameter(Mandatory = $true)]
    [string[]] $CommandArgs
  )

  $useComposeV2 = $false
  & $dockerExe @("compose", "version") *>$null
  if ($LASTEXITCODE -eq 0) {
    $useComposeV2 = $true
  }

  if ($useComposeV2) {
    $all = @("compose", "-f", $ComposeFile) + $CommandArgs
    & $dockerExe @all
  }
  elseif (Get-Command docker-compose.exe -ErrorAction SilentlyContinue) {
    $dc = (Get-Command docker-compose.exe).Source
    $all = @("-f", $ComposeFile) + $CommandArgs
    & $dc @all
  }
  elseif (Get-Command docker-compose -ErrorAction SilentlyContinue) {
    $dc = (Get-Command docker-compose).Source
    $all = @("-f", $ComposeFile) + $CommandArgs
    & $dc @all
  }
  else {
    Write-Error "Docker Compose not found. Install Docker Desktop (Compose V2) or docker-compose v1."
  }

  if ($LASTEXITCODE -ne 0) {
    throw "Docker compose failed (exit code $LASTEXITCODE)."
  }
}

switch ($Action) {
  "down" {
    Write-Host "Stopping stack in: $Root"
    Invoke-NightmareCompose -CommandArgs @("down", "--remove-orphans")
    Write-Host "Stopped."
  }
  "logs" {
    Write-Host "Following logs (Ctrl+C stops tail only). Project: $Root"
    Invoke-NightmareCompose -CommandArgs @("logs", "-f")
  }
  "ps" {
    Invoke-NightmareCompose -CommandArgs @("ps")
  }
  default {
    Write-Host "Building and starting stack from: $Root"
    Invoke-NightmareCompose -CommandArgs @("up", "-d", "--build", "--remove-orphans")
    Write-Host ""
    Write-Host "Stack is up. URLs:"
    Write-Host "  Command Center   http://localhost:8080/"
    Write-Host "  RabbitMQ UI    http://localhost:15672/  (nightmare / nightmare)"
    Write-Host "  Postgres       localhost:5432  db=nightmare_v2  user=nightmare"
    Write-Host "  Redis          localhost:6379"
    Write-Host ""
    Write-Host "Debug:"
    Write-Host "  .\deploy\run-local.ps1 -Action logs"
    Write-Host "  .\deploy\run-local.ps1 -Action ps"
    Write-Host "  .\deploy\run-local.ps1 -Action down"
    Write-Host "  docker compose -f deploy/docker-compose.yml logs -f command-center worker-spider"
  }
}
