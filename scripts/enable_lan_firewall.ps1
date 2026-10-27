$ErrorActionPreference = 'Stop'

$ruleName = 'TROPOS LAN Demo 8765'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$config = Get-Content -Raw -LiteralPath (Join-Path $root '.local\lan-access.json') | ConvertFrom-Json
$pythonw = (Get-Command pythonw).Source
$statusFile = Join-Path $root '.local\firewall-ready.txt'
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue

if ($null -eq $existing) {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8765 `
        -LocalAddress $config.allowedHost `
        -RemoteAddress LocalSubnet `
        -Program $pythonw `
        -Profile Any | Out-Null
} else {
    Set-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Any | Out-Null
    Get-NetFirewallRule -DisplayName $ruleName |
        Get-NetFirewallPortFilter |
        Set-NetFirewallPortFilter -Protocol TCP -LocalPort 8765 | Out-Null
    Get-NetFirewallRule -DisplayName $ruleName |
        Get-NetFirewallAddressFilter |
        Set-NetFirewallAddressFilter `
            -LocalAddress $config.allowedHost `
            -RemoteAddress LocalSubnet | Out-Null
    Get-NetFirewallRule -DisplayName $ruleName |
        Get-NetFirewallApplicationFilter |
        Set-NetFirewallApplicationFilter -Program $pythonw | Out-Null
}

Set-Content -LiteralPath $statusFile -Value 'ready' -Encoding ascii
