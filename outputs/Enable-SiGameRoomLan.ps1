#Requires -RunAsAdministrator
[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$rule = 'Si-Game-Room-LAN-Test-8765'
if ($Remove) {
    Get-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    return
}

$room = Join-Path (Split-Path -Parent $PSScriptRoot) 'work\si-game-room'
$manifest = Get-Content -LiteralPath (Join-Path $room '.runtime\bridge.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$origin = [uri]$manifest.origin
$address = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $origin.Host | Select-Object -First 1
if ($origin.Scheme -ne 'https' -or !$address) { throw 'Start the local game room first.' }
$listener = Get-NetTCPConnection -State Listen -LocalAddress $origin.Host -LocalPort $origin.Port | Select-Object -First 1
if (!$listener) { throw 'Start the local game room first.' }
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
if (!$process.ExecutablePath -or $process.CommandLine -notmatch [regex]::Escape((Join-Path $room 'server.py'))) {
    throw 'The HTTPS listener is not the project game room.'
}
$network = [Net.IPAddress]::Parse($address.IPAddress).GetAddressBytes()
for ($index = 0; $index -lt $network.Length; $index++) {
    $bits = [Math]::Min(8, [Math]::Max(0, $address.PrefixLength - 8 * $index))
    $network[$index] = $network[$index] -band (256 - (1 -shl (8 - $bits)))
}
$subnet = "$($network -join '.')/$($address.PrefixLength)"
$profile = (Get-NetConnectionProfile -InterfaceIndex $address.InterfaceIndex).NetworkCategory
if ($profile -eq 'DomainAuthenticated') { $profile = 'Domain' }
if (!$profile -or $profile -notin 'Domain','Private','Public') { throw 'Cannot identify the active network profile.' }

Get-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name $rule -DisplayName 'Si Game Room (LAN test)' -Direction Inbound -Action Allow `
    -Profile $profile -InterfaceAlias $address.InterfaceAlias -Protocol TCP -LocalAddress $origin.Host `
    -RemoteAddress $subnet -LocalPort $origin.Port -Program $process.ExecutablePath |
    Select-Object DisplayName, Enabled, Profile, Direction, Action
