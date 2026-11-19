# Looks up a group by name in the logged-in WeChat Native account.
[CmdletBinding()]
param([Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$GroupName)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $workspace 'work\wechat-native\wsl-connection.json'
$wx = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$cli = @(
    '-d', $wx.distro, '--exec', "$($wx.path)/wx"
    '--server-config', "$($wx.path)/server.toml"
    '--config', "$($wx.path)/client.toml", '--account', $wx.account, '--json'
)

function Read-WeChatJson {
    $lines = & wsl.exe @cli @args
    if ($LASTEXITCODE -ne 0) { throw "WeChat Native returned exit code $LASTEXITCODE." }
    if (!$lines) { throw 'WeChat Native returned no JSON.' }
    ($lines -join "`n") | ConvertFrom-Json
}

$previousEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $result = Read-WeChatJson groups find $GroupName
    if (!$result.items) {
        $status = Read-WeChatJson auth status
        if ($status.connection -ne 'online') {
            Write-Warning 'No local match. WeChat is offline; reconnect before refreshing the directory.'
            return
        }
        Write-Host 'No local match. Refreshing the WeChat group directory once...'
        $sync = Read-WeChatJson groups sync --wait 20
        if ($sync.state -ne 'succeeded') {
            Write-Warning "The group directory refresh did not finish (state: $($sync.state)). Try again shortly."
            return
        }
        $result = Read-WeChatJson groups find $GroupName
    }
    if (!$result.items) {
        Write-Warning 'No matching group after refresh. Check that the bot account is in the group and try a shorter name.'
        return
    }
    $result.items | Select-Object displayName,roomId
} finally {
    [Console]::OutputEncoding = $previousEncoding
}
