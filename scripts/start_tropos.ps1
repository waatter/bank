param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$local = Join-Path $root '.local'
$launcher = Join-Path $PSScriptRoot 'run_windows.py'
$healthUrl = 'http://127.0.0.1:8765/api/health'
$defaultUrl = 'http://127.0.0.1:8765/demo.html'

New-Item -ItemType Directory -Force -Path $local | Out-Null

# Do not let a stale system proxy block the localhost health check.
$previousProxy = [System.Net.WebRequest]::DefaultWebProxy
[System.Net.WebRequest]::DefaultWebProxy = $null

function Test-TroposHealth {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
        return $health.ok -eq $true -and $health.service -eq 'TROPOS synthetic bank'
    }
    catch {
        return $false
    }
}

function Test-LocalPort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', 8765)
        return $task.Wait(250) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Resolve-Python {
    $commands = @(
        @{ Name = 'py.exe'; Prefix = @('-3') },
        @{ Name = 'python.exe'; Prefix = @() },
        @{ Name = 'python3.exe'; Prefix = @() }
    )
    foreach ($candidate in $commands) {
        if (-not (Get-Command $candidate.Name -ErrorAction SilentlyContinue)) { continue }
        try {
            $probe = @($candidate.Prefix) + @('-c', 'import sys; print(sys.executable); print(int(sys.version_info >= (3, 10)))')
            $lines = & $candidate.Name @probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $lines.Count -ge 2 -and $lines[-1].Trim() -eq '1') {
                return [pscustomobject]@{ File = $lines[-2].Trim() }
            }
        }
        catch { }
    }
    throw 'Python 3.10 or newer was not found. Install Python and enable Add Python to PATH.'
}

function Get-DemoUrl {
    $configPath = Join-Path $local 'lan-access.json'
    if (Test-Path -LiteralPath $configPath) {
        try {
            $config = Get-Content -Raw -LiteralPath $configPath -Encoding UTF8 | ConvertFrom-Json
            if ($config.accessToken -is [string] -and $config.accessToken -match '^[A-Za-z0-9_-]{24,128}$') {
                return $defaultUrl + '?access=' + [uri]::EscapeDataString($config.accessToken)
            }
        }
        catch { }
    }
    return $defaultUrl
}

try {
    if (-not (Test-TroposHealth)) {
        if (Test-LocalPort) {
            throw 'Port 8765 is in use by another program. Close that program and retry.'
        }

        $python = Resolve-Python
        Write-Host 'Starting TROPOS. Please wait...' -ForegroundColor Cyan
        $process = Start-Process -FilePath $python.File `
            -ArgumentList ('"' + $launcher + '"') `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru
        Set-Content -LiteralPath (Join-Path $local 'server.pid') -Value $process.Id -Encoding ASCII

        $ready = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            Start-Sleep -Milliseconds 500
            if (Test-TroposHealth) { $ready = $true; break }
            if ($process.HasExited) { break }
        }
        if (-not $ready) {
            $errorLog = Join-Path $local 'server.err.log'
            $tail = if (Test-Path -LiteralPath $errorLog) {
                (Get-Content -LiteralPath $errorLog -Tail 12 -Encoding UTF8) -join [Environment]::NewLine
            } else { 'No error log was created.' }
            throw "TROPOS did not become healthy within 30 seconds. Error log:`n$tail"
        }
    }

    $url = Get-DemoUrl
    Write-Host 'TROPOS is ready: ' -NoNewline -ForegroundColor Green
    Write-Host $url
    if (-not $NoBrowser) {
        Start-Process $url
    }
    exit 0
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
finally {
    [System.Net.WebRequest]::DefaultWebProxy = $previousProxy
}
