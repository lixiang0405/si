[CmdletBinding()]
param(
    [string]$WorkspaceRoot
)

$ErrorActionPreference = 'Stop'
if (!$WorkspaceRoot) { $WorkspaceRoot = Split-Path -Parent $PSScriptRoot }
$workspace = (Resolve-Path -LiteralPath $WorkspaceRoot).ProviderPath
$data = Join-Path $workspace 'work\astrbot-instance\data'
$config = Get-Content -LiteralPath (Join-Path $data 'cmd_config.json') -Raw -Encoding utf8 | ConvertFrom-Json
$botIds = @($config.platform | Where-Object { $_.enable -and $_.type -in 'aiocqhttp','wechat_native' })
if (!$botIds) { throw 'No enabled QQ or WeChat bot was found.' }
$directory = Join-Path $data 'plugin_data\astrbot_plugin_qoder_neko'
[IO.Directory]::CreateDirectory($directory) | Out-Null
$output = Join-Path $directory 'model-pairing.json'
$temp = Join-Path $directory ('.model-pairing-' + [guid]::NewGuid().ToString('N') + '.tmp')
$backup = $temp + '.bak'
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$sha = [Security.Cryptography.SHA256]::Create()
$entries = @{}
$commands = @()
$expiresAt = [DateTimeOffset]::UtcNow.AddMinutes(30).ToUnixTimeSeconds()
$command = '/' + [char]0x6A21 + [char]0x578B + ' ' + [char]0x7ED1 + [char]0x5B9A
try {
    foreach ($bot in $botIds) {
        $bytes = [byte[]]::new(6)
        $rng.GetBytes($bytes)
        $code = [BitConverter]::ToString($bytes).Replace('-', '')
        $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($code))).Replace('-', '').ToLowerInvariant()
        $entries[$bot.id] = @{ sha256 = $hash; expires_at = $expiresAt }
        $commands += '{0} ({1})  {2} {3}' -f $bot.id, $bot.type, $command, $code
    }
    [IO.File]::WriteAllText($temp, ($entries | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $output) {
        [IO.File]::Replace($temp, $output, $backup)
    } else {
        [IO.File]::Move($temp, $output)
    }
} finally {
    $sha.Dispose()
    $rng.Dispose()
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
    if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup }
}
$commands
Write-Output 'Send the matching command from your own account. Each code expires in 30 minutes.'
