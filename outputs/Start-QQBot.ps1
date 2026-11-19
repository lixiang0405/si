# Starts the local AstrBot, NapCat and installed WeChat Native services on Windows.
[CmdletBinding()]
param(
    [string]$WorkspaceRoot,
    [switch]$Status,
    [switch]$QQLogin,
    [switch]$WeChatLogin,
    [switch]$NoLoginPopup,
    [switch]$StopAstrBotForDeploy,
    [switch]$AllowSleep,
    [string]$WeChatDeviceName = ('{0}{1}WeChat Native{2}' -f [char]0x7940, [char]0xFF08, [char]0xFF09)
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Start-SiQQLogin.ps1')
. (Join-Path $PSScriptRoot 'Start-SiWeChatLogin.ps1')

function Read-JsonConfig {
    param([string]$Path)
    try {
        Get-Content -LiteralPath $Path -Encoding UTF8 -Raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Could not read UTF-8 JSON from $Path."
    }
}

function Get-CodexCliDirectory {
    $command = Get-Command -Name codex -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return (Split-Path -Parent $command.Source)
    }
    try {
        $packages = Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction Stop |
            Sort-Object -Property Version -Descending
        foreach ($package in $packages) {
            if (!$package.InstallLocation) { continue }
            $directory = Join-Path $package.InstallLocation 'app\resources'
            if (Test-Path -LiteralPath (Join-Path $directory 'codex.exe') -PathType Leaf) {
                return $directory
            }
        }
    } catch {
        Write-Verbose 'Could not resolve the registered Codex desktop app.'
    }
}

if (!$WorkspaceRoot) { $WorkspaceRoot = Split-Path -Parent $PSScriptRoot }
$WorkspaceRoot = (Resolve-Path -LiteralPath $WorkspaceRoot).ProviderPath
$work = Join-Path $WorkspaceRoot 'work'
$astrRoot = Join-Path $work 'astrbot-instance'
$astrExe = Join-Path $work 'astrbot-venv\Scripts\python.exe'
$astrConfigPath = Join-Path $astrRoot 'data\cmd_config.json'
$napRoot = Join-Path $work 'napcat'
$napPortable = Join-Path $napRoot 'node-portable'
$napExe = Join-Path $napPortable 'node.exe'
$napEntry = Join-Path $napPortable 'index.js'
$napRuntime = Join-Path $napRoot 'runtime-node'
$napConfigPath = Join-Path $napRuntime 'config\webui.json'
$napUserData = Join-Path $napRoot 'node-qq-data'
$logDir = Join-Path $work 'launch-logs'
$wechatRoot = Join-Path $work 'wechat-native'
$wechatWslPath = Join-Path $wechatRoot 'wsl-connection.json'
$wechatWsl = if (Test-Path -LiteralPath $wechatWslPath -PathType Leaf) {
    Read-JsonConfig $wechatWslPath
}
$wechatExe = Join-Path $wechatRoot 'wx.exe'
$wechatConfig = Join-Path $wechatRoot 'server.toml'
$hasWeChat = $wechatWsl -or ((Test-Path -LiteralPath $wechatExe -PathType Leaf) -and
    (Test-Path -LiteralPath $wechatConfig -PathType Leaf))
$wechatLauncher = $wechatExe
$wechatDaemonArgs = @('--server-config', $wechatConfig, '--data-dir', (Join-Path $wechatRoot 'state'))
$wechatAccountArgs = $wechatDaemonArgs + @('--config', (Join-Path $wechatRoot 'client.toml'), '--account', 'si')
$wechatAccount = 'si'
$wechatPort = 6174
if ($wechatWsl) {
    $wechatLauncher = 'wsl.exe'
    $wechatPort = [int]$wechatWsl.port
    $wechatDaemonArgs = @('-d', $wechatWsl.distro, '--exec', "$($wechatWsl.path)/wx",
        '--server-config', "$($wechatWsl.path)/server.toml")
    $wechatAccount = $wechatWsl.account
    $wechatAccountArgs = $wechatDaemonArgs + @('--config', "$($wechatWsl.path)/client.toml", '--account', $wechatWsl.account)
}
if (($Status -or $NoLoginPopup) -and ($QQLogin -or $WeChatLogin)) {
    throw 'Run -QQLogin and -WeChatLogin separately from -Status and -NoLoginPopup.'
}
if ($WeChatLogin -and !$hasWeChat) {
    throw 'WeChat Native is not installed in this workspace.'
}

function Invoke-WeChatCli {
    param([string[]]$Arguments)
    # Windows PowerShell can turn a WSL stderr warning into a terminating error.
    $ErrorActionPreference = 'Continue'
    & $wechatLauncher @Arguments 2>$null
}

function Get-WeChatAccountStatus {
    try {
        $accountJson = Invoke-WeChatCli -Arguments ($wechatAccountArgs + @('--json', 'auth', 'status'))
        if ($LASTEXITCODE -eq 0 -and $accountJson) {
            ($accountJson | Out-String) | ConvertFrom-Json -ErrorAction Stop
        }
    } catch {
        # An inaccessible daemon or invalid status is not a request for a new login.
    }
}

function Open-WeChatLogin {
    if ($wechatWsl) {
        $tokenPath = "$($wechatWsl.path)/state/api-token"
        $ownerToken = (Invoke-WeChatCli -Arguments @('-d', $wechatWsl.distro, '--exec', 'cat', $tokenPath) | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read the local Native owner token.' }
    } else {
        $localTokenPath = Join-Path $wechatRoot 'state\api-token'
        if (!(Test-Path -LiteralPath $localTokenPath -PathType Leaf)) { throw 'Cannot read the local Native owner token.' }
        $ownerToken = [IO.File]::ReadAllText($localTokenPath).Trim()
    }
    if (!$ownerToken) { throw 'The local Native owner token is empty.' }

    $accountUrl = 'http://127.0.0.1:{0}/v1/accounts/{1}' -f $wechatPort, [uri]::EscapeDataString($wechatAccount)
    $qrDir = Join-Path $wechatRoot 'startup-login'
    try {
        $loginLock = [IO.File]::Open((Join-Path $logDir 'wechat-login.lock'), 'OpenOrCreate', 'Write', 'None')
    } catch [IO.IOException] {
        Write-Warning 'A WeChat login window is already running. Use the existing window.'
        return
    }
    try {
        Show-WeChatLogin -OwnerToken $ownerToken -AccountUrl $accountUrl -DeviceName $WeChatDeviceName -ImageDirectory $qrDir -ForceQr:$WeChatLogin
    } finally {
        $loginLock.Dispose()
    }
}

foreach ($path in $astrExe, $astrConfigPath, $napExe, $napEntry, $napConfigPath) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing $path. Use -WorkspaceRoot with the folder containing the existing work directory."
    }
}

$astrConfig = Read-JsonConfig $astrConfigPath
$napConfig = Read-JsonConfig $napConfigPath
$oneBot = $astrConfig.platform | Where-Object { $_.id -eq 'qq-personal' } | Select-Object -First 1
$wechatEnabled = $hasWeChat -and @($astrConfig.platform | Where-Object { $_.type -eq 'wechat_native' -and $_.enable -eq $true }).Count -gt 0
$astrPort = [int]$astrConfig.dashboard.port
$napPort = [int]$napConfig.port
$wsPort = [int]$oneBot.ws_reverse_port
if ($astrPort -le 0 -or $napPort -le 0 -or ($oneBot.enable -and $wsPort -le 0)) {
    throw 'Check the AstrBot, NapCat and qq-personal ports in their local configuration.'
}
$scheme = 'http'
if ($astrConfig.dashboard.ssl_enable -eq $true) { $scheme = 'https' }
$astrUrl = '{0}://127.0.0.1:{1}/' -f $scheme, $astrPort
$napUrl = 'http://127.0.0.1:{0}/webui' -f $napPort

function Get-ServiceListener {
    param([int]$Port, [string]$Name)

    $sockets = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($socket in $sockets) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($socket.OwningProcess)"
        if ($Name -eq 'NapCat') {
            $owned = $owner.ExecutablePath -eq $napExe
        } else {
            # A Windows venv launcher delegates the port to its base Python child.
            $command = [string]$owner.CommandLine
            $venvCommand = $command.StartsWith(('"' + $astrExe + '" '), [StringComparison]::OrdinalIgnoreCase)
            $owned = ($owner.ExecutablePath -eq $astrExe -or $venvCommand) -and
                $command -match '\s-m\s+astrbot\.cli(?:\.__main__)?\s+run(?:\s|$)'
        }
        if ($owned -and $socket.LocalAddress -in '127.0.0.1', '0.0.0.0', '::') {
            return $socket.OwningProcess
        }
    }
    if ($sockets.Count) {
        throw ('Port {0} is occupied or is not accessible on localhost. Check the port before starting {1}.' -f $Port, $Name)
    }
}

if ($StopAstrBotForDeploy) {
    $botId = Get-ServiceListener -Port $astrPort -Name 'AstrBot'
    if ($oneBot.enable) {
        $oneBotId = Get-ServiceListener -Port $wsPort -Name 'AstrBot'
        if ($botId -and $oneBotId -and $botId -ne $oneBotId) {
            throw 'The AstrBot ports have different owners.'
        }
        if (!$botId) { $botId = $oneBotId }
    }
    if ($botId) {
        $bot = Get-Process -Id $botId -ErrorAction Stop
        if (Get-CimInstance Win32_Process -Filter "ParentProcessId=$botId AND Name='codex.exe'") {
            throw 'A local Codex turn is active. Try deployment again after it finishes.'
        }
        Stop-Process -InputObject $bot -ErrorAction Stop
        Wait-Process -InputObject $bot -Timeout 20 -ErrorAction Stop
    }
    return
}

function Get-KeepAwakeIdentity {
    param([Diagnostics.Process]$Bot)
    "Local\SiBotAwake-$($Bot.Id)-$($Bot.StartTime.ToUniversalTime().Ticks)"
}

function Open-KeepAwakeEvent {
    param([string]$Name)
    try { [Threading.EventWaitHandle]::OpenExisting($Name) }
    catch [Threading.WaitHandleCannotBeOpenedException] { $null }
}

function Test-BotKeepAwake {
    param([string]$Identity)
    $ready = Open-KeepAwakeEvent "$Identity-ready"
    if (!$ready) { return $false }
    try { $ready.WaitOne(0) } finally { $ready.Dispose() }
}

function Set-BotKeepAwake {
    param([int]$BotProcessId, [bool]$Enabled, [string]$HelperPath, [string]$LogDirectory)
    $bot = Get-Process -Id $BotProcessId -ErrorAction SilentlyContinue
    if (!$bot) { return $false }
    try {
        $identity = Get-KeepAwakeIdentity $bot
        if (!$Enabled) {
            $stop = Open-KeepAwakeEvent "$identity-stop"
            if ($stop) {
                try { $null = $stop.Set() } finally { $stop.Dispose() }
                $timer = [Diagnostics.Stopwatch]::StartNew()
                while ((Test-BotKeepAwake $identity) -and $timer.Elapsed.TotalSeconds -lt 5) {
                    Start-Sleep -Milliseconds 100
                }
            }
            return !(Test-BotKeepAwake $identity)
        }
        if (Test-BotKeepAwake $identity) { return $true }

        $powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $ticks = $bot.StartTime.ToUniversalTime().Ticks
        $logStem = Join-Path $LogDirectory ("KeepAwake-$BotProcessId-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
        $launch = @{
            FilePath = $powerShell
            ArgumentList = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$HelperPath`" -BotProcessId $BotProcessId -BotStartTicks $ticks"
            WindowStyle = 'Hidden'
            PassThru = $true
            RedirectStandardOutput = "$logStem.stdout.log"
            RedirectStandardError = "$logStem.stderr.log"
        }
        $process = Start-Process @launch
        try {
            $timer = [Diagnostics.Stopwatch]::StartNew()
            do {
                if (Test-BotKeepAwake $identity) { return $true }
                if ($process.HasExited -or $bot.HasExited) { break }
                Start-Sleep -Milliseconds 100
            } while ($timer.Elapsed.TotalSeconds -lt 8)
            return $false
        } finally { $process.Dispose() }
    } finally { $bot.Dispose() }
}

function Start-LocalService {
    param(
        [string]$Name, [string]$Executable, [string]$Arguments,
        [string]$Directory, [int]$Port, [hashtable]$Environment
    )

    $listener = Get-ServiceListener -Port $Port -Name $Name
    if ($listener) {
        Write-Output "$Name is already running (PID $listener)."
        return
    }

    $logStem = Join-Path $logDir ("$Name-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    $previous = @{}
    try {
        foreach ($key in $Environment.Keys) {
            $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            [Environment]::SetEnvironmentVariable($key, $Environment[$key], 'Process')
        }
        $launch = @{
            FilePath = $Executable
            ArgumentList = $Arguments
            WorkingDirectory = $Directory
            WindowStyle = 'Hidden'
            PassThru = $true
            RedirectStandardOutput = "$logStem.stdout.log"
            RedirectStandardError = "$logStem.stderr.log"
        }
        $process = Start-Process @launch
    } finally {
        foreach ($key in $previous.Keys) {
            [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
        }
    }

    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $listener = Get-ServiceListener -Port $Port -Name $Name
        if ($listener) {
            Write-Output "$Name is running (PID $listener)."
            return
        }
        if ($process.HasExited) { break }
        Start-Sleep -Milliseconds 500
    } while ($timer.Elapsed.TotalSeconds -lt 45)

    throw "$Name did not open port $Port. Check $logStem.stderr.log and $logStem.stdout.log."
}

if (!$Status) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    try {
        $launchLock = [IO.File]::Open((Join-Path $logDir 'startup.lock'), 'OpenOrCreate', 'Write', 'None')
    } catch [IO.IOException] {
        throw 'Another startup is in progress. Wait for it to finish, then use -Status.'
    }

    try {
        # Check the AstrBot and NapCat ports before startup.
        $astrListener = Get-ServiceListener -Port $astrPort -Name 'AstrBot'
        $null = Get-ServiceListener -Port $napPort -Name 'NapCat'
        if ($oneBot.enable) { $null = Get-ServiceListener -Port $wsPort -Name 'AstrBot' }

        if ($hasWeChat) {
            Invoke-WeChatCli -Arguments ($wechatDaemonArgs + @('daemon', 'start')) | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning 'WeChat Native did not start. Check its daemon log.'
            }
        }

        $astrStart = @{
            Name = 'AstrBot'
            Executable = $astrExe
            Arguments = '-m astrbot.cli run'
            Directory = $astrRoot
            Port = $astrPort
            Environment = @{
                ASTRBOT_ROOT = $astrRoot
                DASHBOARD_HOST = '127.0.0.1'
                DASHBOARD_PORT = [string]$astrPort
                PYTHONUTF8 = '1'
                PYTHONIOENCODING = 'utf-8'
            }
        }
        if (!$astrListener) {
            $codexDirectory = Get-CodexCliDirectory
            if ($codexDirectory) {
                $astrStart.Environment['PATH'] = "$codexDirectory;$env:Path"
            }
        }
        Start-LocalService @astrStart

        New-Item -ItemType Directory -Path $napUserData -Force | Out-Null
        $napStart = @{
            Name = 'NapCat'
            Executable = $napExe
            Arguments = '"' + $napEntry + '"'
            Directory = $napPortable
            Port = $napPort
            Environment = @{
                NAPCAT_WORKDIR = $napRuntime
                NAPCAT_QQ_DATA_DIR = $napUserData
            }
        }
        Start-LocalService @napStart
        $botProcessId = Get-ServiceListener -Port $astrPort -Name 'AstrBot'
        if ($botProcessId) {
            try {
                $helper = Join-Path $PSScriptRoot 'Keep-SiBotAwake.ps1'
                if (!(Set-BotKeepAwake -BotProcessId $botProcessId -Enabled (!$AllowSleep) -HelperPath $helper -LogDirectory $logDir)) {
                    Write-Warning "Could not update the AstrBot sleep request. Check the KeepAwake log in $logDir."
                }
            } catch {
                Write-Warning "Could not update the AstrBot sleep request. Check the KeepAwake log in $logDir."
            }
        }
    } finally {
        $launchLock.Dispose()
    }
}

$napListener = $null
$astrListener = $null
foreach ($service in @(@{Name='AstrBot'; Port=$astrPort; Url=$astrUrl}, @{Name='NapCat'; Port=$napPort; Url=$napUrl})) {
    $listener = Get-ServiceListener -Port $service.Port -Name $service.Name
    $state = 'stopped'
    if ($listener) { $state = "running (PID $listener)" }
    if ($service.Name -eq 'NapCat') { $napListener = $listener }
    if ($service.Name -eq 'AstrBot') { $astrListener = $listener }
    Write-Output ('{0}  {1}  {2}' -f $service.Name, $state, $service.Url)
}
if ($oneBot.enable) {
    $listener = Get-ServiceListener -Port $wsPort -Name 'AstrBot'
    $connection = if ($listener) {
        Get-NetTCPConnection -LocalPort $wsPort -State Established -OwningProcess $listener -ErrorAction SilentlyContinue |
            Where-Object { $_.RemoteAddress -eq '127.0.0.1' } | Select-Object -First 1
    }
    if ($connection) {
        Write-Output "OneBot v11  TCP connected on port $wsPort"
    } elseif ($listener) {
        Write-Output "OneBot v11  waiting for NapCat on port $wsPort"
    } else {
        Write-Output "OneBot v11  listener stopped on port $wsPort"
    }
} else {
    Write-Output 'OneBot v11  qq-personal is disabled or missing'
}
if ($astrListener) {
    $bot = Get-Process -Id $astrListener -ErrorAction SilentlyContinue
    if ($bot) {
        try {
            if (Test-BotKeepAwake (Get-KeepAwakeIdentity $bot)) {
                Write-Output 'Sleep prevention  active (while AstrBot runs; keep the laptop plugged in)'
            } else {
                Write-Output 'Sleep prevention  inactive'
            }
        } finally { $bot.Dispose() }
    }
}
Write-Output "Startup logs: $logDir"

$qqState = 'stopped'
if ($napListener) {
    $qqAccount = Get-QQLoginState -NapConfig $napConfig
    if (!$Status -and !$NoLoginPopup -and !$WeChatLogin -and !$QQLogin -and
        $qqAccount.State -eq 'starting' -and $qqAccount.Phase -in 'waiting_qrcode', 'generating_qrcode', 'initializing') {
        Start-Sleep -Seconds 2
        $qqAccount = Get-QQLoginState -NapConfig $napConfig
    }
    $qqState = $qqAccount.State
}
Write-Output "QQ account  $qqState"

$wechatState = 'stopped'
if ($hasWeChat) {
    $wechatState = Get-WeChatLoginState -Account (Get-WeChatAccountStatus)
    Write-Output "WeChat Native  $wechatState"
}
if ($Status -or $NoLoginPopup) { return }

# A request for one platform should not open a second login window.
$automaticLogin = !$QQLogin -and !$WeChatLogin
if ($QQLogin -or ($automaticLogin -and $oneBot.enable -and $qqState -eq 'needs_login')) {
    if ($qqState -eq 'online') {
        Write-Output 'QQ is already signed in.'
    } elseif ($napListener) {
        if (Open-QQLogin -NapPort $napPort) {
            Write-Output "QQ login opened in your browser: $napUrl/qq_login"
        } else {
            Write-Warning "Open $napUrl/qq_login in a browser to sign in to QQ."
        }
    } else {
        Write-Warning 'NapCat is stopped. Check its startup logs before logging in to QQ.'
        if ($QQLogin) { exit 1 }
    }
} elseif ($automaticLogin -and $oneBot.enable -and $qqState -in 'unavailable', 'starting') {
    Write-Output 'QQ login not confirmed. Recheck with -Status or open QQ login with -QQLogin.'
}

if ($WeChatLogin -or ($automaticLogin -and $wechatEnabled -and $wechatState -eq 'needs_login')) {
    if ($wechatState -eq 'online') {
        Write-Output 'WeChat is already signed in.'
    } elseif ($wechatState -eq 'unavailable') {
        Write-Warning "WeChat Native status is unavailable. Check WSL and $logDir, then retry -WeChatLogin."
        if ($WeChatLogin) { exit 1 }
    } else {
        try {
            $loggedIn = Open-WeChatLogin
            if ($WeChatLogin -and !$loggedIn) { exit 1 }
        } catch {
            Write-Warning $_.Exception.Message
            Write-Output 'To retry WeChat login, run Start-SiBot.cmd -WeChatLogin.'
            if ($WeChatLogin) { exit 1 }
        }
    }
} elseif ($automaticLogin -and $wechatEnabled -and $wechatState -in 'unavailable', 'reconnecting') {
    Write-Output 'WeChat login not confirmed. Recheck with -Status; use -WeChatLogin if it stays offline.'
}
