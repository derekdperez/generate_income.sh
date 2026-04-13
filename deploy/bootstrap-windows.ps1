[CmdletBinding()]
param(
    [ValidateSet("Central", "Worker")]
    [string]$Role = "Central",
    [string]$BaseUrl = "",
    [string]$ApiToken = "",
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ScriptPaths {
    $scriptDir = $PSScriptRoot
    if (-not $scriptDir) {
        if ($PSCommandPath) {
            $scriptDir = Split-Path -Parent $PSCommandPath
        } else {
            $scriptDir = (Get-Location).Path
        }
    }
    $rootPath = Resolve-Path (Join-Path $scriptDir "..")
    $deployPath = Resolve-Path $scriptDir
    [pscustomobject]@{
        Root   = $rootPath.Path
        Deploy = $deployPath.Path
        Env    = Join-Path $deployPath ".env"
        WorkerEnv = Join-Path $deployPath "worker.env.generated"
        TlsDir = Join-Path $deployPath "tls"
        CertPem = Join-Path $deployPath "tls\server.crt"
        KeyPem  = Join-Path $deployPath "tls\server.key"
    }
}

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $Name"
    }
}

function New-HexToken([int]$ByteCount) {
    $bytes = New-Object byte[] ($ByteCount)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}

function Try-GetEc2Metadata([string]$Path) {
    try {
        $token = Invoke-RestMethod -Method Put -Uri "http://169.254.169.254/latest/api/token" -Headers @{ "X-aws-ec2-metadata-token-ttl-seconds" = "60" } -TimeoutSec 2
        if ($token) {
            return Invoke-RestMethod -Method Get -Uri ("http://169.254.169.254/latest/meta-data/{0}" -f $Path) -Headers @{ "X-aws-ec2-metadata-token" = "$token" } -TimeoutSec 2
        }
    } catch {
        # fall through
    }
    try {
        return Invoke-RestMethod -Method Get -Uri ("http://169.254.169.254/latest/meta-data/{0}" -f $Path) -TimeoutSec 2
    } catch {
        return $null
    }
}

function Get-DetectedBaseUrl([string]$Override = "") {
    if ($Override) {
        return $Override
    }

    $publicHostname = (Try-GetEc2Metadata "public-hostname")
    if ($publicHostname) {
        return "https://$($publicHostname.ToString().Trim())"
    }

    $publicIpv4 = (Try-GetEc2Metadata "public-ipv4")
    if ($publicIpv4) {
        return "https://$($publicIpv4.ToString().Trim())"
    }

    try {
        $ip = (Invoke-RestMethod -Method Get -Uri "https://checkip.amazonaws.com" -TimeoutSec 4).ToString().Trim()
        if ($ip) {
            return "https://$ip"
        }
    } catch {
        # continue
    }

    $name = [System.Net.Dns]::GetHostName()
    if ($name) {
        return "https://$name"
    }

    return "https://127.0.0.1"
}

function Get-HostFromUrl([string]$Url) {
    $u = [System.Uri]::new($Url)
    return $u.Host
}

function New-PemCertificatePair([string]$HostName, [string]$CertFile, [string]$KeyFile, [switch]$Overwrite) {
    $certExists = Test-Path -LiteralPath $CertFile
    $keyExists = Test-Path -LiteralPath $KeyFile
    if ($certExists -and $keyExists -and -not $Overwrite) {
        return
    }

    $tlsDir = Split-Path -Parent $CertFile
    New-Item -ItemType Directory -Path $tlsDir -Force | Out-Null

    $dnsNames = @($HostName, "localhost")
    try {
        $cert = New-SelfSignedCertificate `
            -DnsName $dnsNames `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -FriendlyName "nightmare-local-coordinator" `
            -NotAfter (Get-Date).AddDays(825) `
            -KeyExportPolicy Exportable `
            -HashAlgorithm SHA256

        $x509 = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($cert)
        if (-not ($x509 | Get-Member -Name ExportCertificatePem -ErrorAction SilentlyContinue)) {
            throw "ExportCertificatePem is unavailable in this PowerShell/.NET runtime."
        }
        $certPem = $x509.ExportCertificatePem()

        $rsa = $x509.GetRSAPrivateKey()
        if ($null -eq $rsa) {
            throw "No RSA private key found on generated certificate."
        }
        if (-not ($rsa | Get-Member -Name ExportPkcs8PrivateKeyPem -ErrorAction SilentlyContinue)) {
            throw "ExportPkcs8PrivateKeyPem is unavailable in this PowerShell/.NET runtime."
        }
        $keyPem = $rsa.ExportPkcs8PrivateKeyPem()

        Set-Content -LiteralPath $CertFile -Value $certPem -Encoding ascii
        Set-Content -LiteralPath $KeyFile -Value $keyPem -Encoding ascii
        return
    } catch {
        if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
            throw "Failed to generate PEM cert/key in PowerShell and openssl not found. Install OpenSSL or PowerShell 7+."
        }
        $san = if ($HostName -match '^\d{1,3}(\.\d{1,3}){3}$') {
            "IP:$HostName,DNS:localhost,IP:127.0.0.1"
        } else {
            "DNS:$HostName,DNS:localhost,IP:127.0.0.1"
        }
        & openssl req -x509 -newkey rsa:4096 -sha256 -nodes `
            -keyout $KeyFile `
            -out $CertFile `
            -days 825 `
            -subj "/CN=$HostName" `
            -addext "subjectAltName=$san" | Out-Null
    }
}

function Write-EnvFile([string]$Path, [hashtable]$Pairs) {
    $lines = @()
    foreach ($k in $Pairs.Keys) {
        $lines += ("{0}={1}" -f $k, $Pairs[$k])
    }
    Set-Content -LiteralPath $Path -Value ($lines -join [Environment]::NewLine) -Encoding ascii
}

function Start-Central([pscustomobject]$Paths, [string]$BaseUrlOverride, [switch]$OverwriteSecrets) {
    Require-Command "docker"

    $postgresDb = "nightmare"
    $postgresUser = "nightmare"
    $postgresPassword = New-HexToken -ByteCount 32
    $coordinatorToken = New-HexToken -ByteCount 48
    $coordinatorBaseUrl = Get-DetectedBaseUrl -Override $BaseUrlOverride
    $baseHost = Get-HostFromUrl -Url $coordinatorBaseUrl

    New-PemCertificatePair -HostName $baseHost -CertFile $Paths.CertPem -KeyFile $Paths.KeyPem -Overwrite:$OverwriteSecrets

    $centralEnv = [ordered]@{
        POSTGRES_DB = $postgresDb
        POSTGRES_USER = $postgresUser
        POSTGRES_PASSWORD = $postgresPassword
        COORDINATOR_API_TOKEN = $coordinatorToken
        TLS_CERT_FILE = (Resolve-Path $Paths.CertPem).Path
        TLS_KEY_FILE = (Resolve-Path $Paths.KeyPem).Path
        COORDINATOR_BASE_URL = $coordinatorBaseUrl
    }
    Write-EnvFile -Path $Paths.Env -Pairs $centralEnv

    $workerEnv = [ordered]@{
        COORDINATOR_BASE_URL = $coordinatorBaseUrl
        COORDINATOR_API_TOKEN = $coordinatorToken
    }
    Write-EnvFile -Path $Paths.WorkerEnv -Pairs $workerEnv

    Push-Location $Paths.Deploy
    try {
        & docker compose -f docker-compose.central.yml --env-file .env up -d --build
    } finally {
        Pop-Location
    }

    Write-Host "Central stack started."
    Write-Host "Generated: $($Paths.Env)"
    Write-Host "Generated: $($Paths.WorkerEnv)"
    Write-Host "Generated: $($Paths.CertPem)"
    Write-Host "Generated: $($Paths.KeyPem)"
}

function Start-Worker([pscustomobject]$Paths, [string]$BaseUrlOverride, [string]$TokenOverride) {
    Require-Command "docker"
    $baseUrl = $BaseUrlOverride
    $token = $TokenOverride

    if ((-not $baseUrl -or -not $token) -and (Test-Path -LiteralPath $Paths.WorkerEnv)) {
        $rawLines = Get-Content -LiteralPath $Paths.WorkerEnv
        foreach ($line in $rawLines) {
            if ($line -match '^COORDINATOR_BASE_URL=(.+)$' -and -not $baseUrl) {
                $baseUrl = $matches[1].Trim()
            }
            if ($line -match '^COORDINATOR_API_TOKEN=(.+)$' -and -not $token) {
                $token = $matches[1].Trim()
            }
        }
    }

    if (-not $baseUrl -or -not $token) {
        throw "Worker setup requires -BaseUrl and -ApiToken, or deploy/worker.env.generated."
    }

    $workerEnv = [ordered]@{
        COORDINATOR_BASE_URL = $baseUrl
        COORDINATOR_API_TOKEN = $token
    }
    Write-EnvFile -Path $Paths.Env -Pairs $workerEnv

    Push-Location $Paths.Deploy
    try {
        & docker compose -f docker-compose.worker.yml --env-file .env up -d --build
    } finally {
        Pop-Location
    }

    Write-Host "Worker started with coordinator: $baseUrl"
    Write-Host "Generated: $($Paths.Env)"
}

$paths = Get-ScriptPaths
switch ($Role) {
    "Central" {
        Start-Central -Paths $paths -BaseUrlOverride $BaseUrl -OverwriteSecrets:$Force
        break
    }
    "Worker" {
        Start-Worker -Paths $paths -BaseUrlOverride $BaseUrl -TokenOverride $ApiToken
        break
    }
    default {
        throw "Unsupported role: $Role"
    }
}
