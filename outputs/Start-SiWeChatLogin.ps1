# Local WeChat Native sign-in for Start-QQBot.ps1.

function Get-WeChatLoginState {
    param($Account)
    if ($Account.authenticated -isnot [bool]) { return 'unavailable' }
    switch ($Account.connection) {
        'online' {
            if ($Account.authenticated) { return 'online' }
            return 'unavailable'
        }
        'reconnecting' { return 'reconnecting' }
        'disconnected' {
            if ($Account.authenticated) { return 'reconnecting' }
            return 'needs_login'
        }
        default { return 'unavailable' }
    }
}

function New-WeChatQrWindow {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $window = New-Object System.Windows.Forms.Form
    $window.Text = '祀 · 微信登录'
    $window.ClientSize = New-Object System.Drawing.Size(380, 412)
    $window.FormBorderStyle = 'FixedDialog'
    $window.MaximizeBox = $false
    $window.StartPosition = 'CenterScreen'
    $window.BackColor = [Drawing.Color]::White
    $caption = New-Object System.Windows.Forms.Label
    $caption.Location = New-Object System.Drawing.Point(12, 12)
    $caption.Size = New-Object System.Drawing.Size(356, 42)
    $caption.TextAlign = 'MiddleCenter'
    $picture = New-Object System.Windows.Forms.PictureBox
    $picture.Location = New-Object System.Drawing.Point(16, 58)
    $picture.Size = New-Object System.Drawing.Size(348, 338)
    $picture.SizeMode = 'Zoom'
    $window.Controls.Add($caption)
    $window.Controls.Add($picture)
    $window.Add_FormClosed({
        param($sender, $eventArgs)
        foreach ($control in $sender.Controls) {
            if ($control -is [Windows.Forms.PictureBox] -and $control.Image) {
                $image = $control.Image
                $control.Image = $null
                $image.Dispose()
            }
        }
    })
    return @{ Form = $window; Picture = $picture; Caption = $caption }
}

function Set-WeChatQrWindow {
    param($Viewer, [string]$Caption, [string]$QrPath)
    $newImage = $null
    if ($QrPath) {
        $file = Get-Item -LiteralPath $QrPath
        if ($file.Length -lt 32 -or $file.Length -gt 2MB) { throw 'Native 返回的二维码文件大小无效。' }
        $bytes = [IO.File]::ReadAllBytes($file.FullName)
        if ([BitConverter]::ToString($bytes, 0, 8) -ne '89-50-4E-47-0D-0A-1A-0A') {
            throw 'Native 返回的二维码不是 PNG。'
        }
        $stream = [IO.MemoryStream]::new($bytes, $false)
        try {
            $image = [Drawing.Image]::FromStream($stream, $false, $true)
            try {
                if ($image.Width -ne $image.Height -or $image.Width -lt 21 -or $image.Width -gt 2048) {
                    throw 'Native 返回的二维码尺寸无效。'
                }
                $newImage = [Drawing.Bitmap]::new($image)
            } finally { $image.Dispose() }
        } finally { $stream.Dispose() }
    }
    $oldImage = $Viewer.Picture.Image
    $Viewer.Picture.Image = $newImage
    if ($oldImage) { $oldImage.Dispose() }
    $Viewer.Caption.Text = $Caption
    if (!$Viewer.Form.Visible) {
        $Viewer.Form.TopMost = $true
        $Viewer.Form.Show()
        $null = $Viewer.Form.Activate()
        $Viewer.Form.TopMost = $false
    }
    [Windows.Forms.Application]::DoEvents()
}

function Test-WeChatQrWindowClosed {
    param($Viewer)
    [Windows.Forms.Application]::DoEvents()
    return $Viewer.Form.IsDisposed
}

function Close-WeChatQrWindow {
    param($Viewer)
    if (!$Viewer.Form.IsDisposed) {
        $Viewer.Form.Close()
        $Viewer.Form.Dispose()
    }
}

function Get-WeChatHttpStatus {
    param($Failure)
    for ($exception = $Failure.Exception; $exception; $exception = $exception.InnerException) {
        if ($exception -is [Net.WebException] -and $exception.Response -and $exception.Response.StatusCode) {
            return [int]$exception.Response.StatusCode
        }
    }
    return 0
}

function Test-WeChatTransportError {
    param($Failure)
    for ($exception = $Failure.Exception; $exception; $exception = $exception.InnerException) {
        if ($exception -is [Net.WebException] -or $exception -is [IO.IOException]) { return $true }
    }
    return $false
}

function Invoke-WeChatNativeRequest {
    param(
        [uri]$Uri, [hashtable]$Headers, [int]$TimeoutSec,
        [string]$Method = 'GET', [byte[]]$Body, [string]$OutFile
    )
    if ($Uri.Scheme -ne 'http' -or $Uri.Host -ne '127.0.0.1' -or $Uri.UserInfo) {
        throw '微信登录请求只允许访问本机 Native 服务。'
    }
    $request = [Net.HttpWebRequest][Net.WebRequest]::Create($Uri)
    $request.Method = $Method
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = $TimeoutSec * 1000
    $request.ReadWriteTimeout = $request.Timeout
    foreach ($key in $Headers.Keys) { $request.Headers[$key] = $Headers[$key] }
    if ($Body) {
        $request.ContentType = 'application/json; charset=utf-8'
        $request.ContentLength = $Body.Length
        $stream = $request.GetRequestStream()
        try { $stream.Write($Body, 0, $Body.Length) } finally { $stream.Dispose() }
    } elseif ($Method -eq 'POST') {
        $request.ContentLength = 0
    }
    $response = $request.GetResponse()
    try {
        if ([int]$response.StatusCode -notin 200, 202) { throw 'Native 返回了非预期的 HTTP 状态。' }
        if ($OutFile -and ($response.ContentType -split ';',2)[0] -ine 'image/png') {
            throw 'Native 返回的二维码类型无效。'
        }
        $limit = if ($OutFile) { 2MB } else { 128KB }
        if ($response.ContentLength -gt $limit) { throw 'Native 登录响应过大。' }
        $stream = $response.GetResponseStream()
        $output = [IO.MemoryStream]::new()
        try {
            $buffer = [byte[]]::new(8192)
            while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                if ($output.Length + $count -gt $limit) { throw 'Native 登录响应过大。' }
                $output.Write($buffer, 0, $count)
            }
            if ($OutFile) {
                [IO.File]::WriteAllBytes($OutFile, $output.ToArray())
            } else {
                [Text.Encoding]::UTF8.GetString($output.ToArray()) | ConvertFrom-Json -ErrorAction Stop
            }
        } finally { $output.Dispose(); $stream.Dispose() }
    } finally { $response.Dispose() }
}

function Show-WeChatLogin {
    param(
        [Parameter(Mandatory)][string]$OwnerToken,
        [Parameter(Mandatory)][uri]$AccountUrl,
        [Parameter(Mandatory)][string]$DeviceName,
        [Parameter(Mandatory)][string]$ImageDirectory,
        [switch]$ForceQr
    )
    if ($AccountUrl.Scheme -ne 'http' -or $AccountUrl.Host -ne '127.0.0.1' -or
        $AccountUrl.UserInfo -or $AccountUrl.Query -or $AccountUrl.Fragment -or
        $AccountUrl.AbsolutePath -notmatch '^/v1/accounts/[A-Za-z0-9_-]+$') {
        throw '微信登录只接受有效的本机 Native 账号地址。'
    }
    if ([Text.Encoding]::UTF8.GetByteCount($DeviceName.Trim()) -notin 1..100 -or $DeviceName -match '[\x00-\x1f\x7f]') {
        throw '微信设备名须为 1 到 100 个 UTF-8 字节，且不要包含控制字符。'
    }
    $headers = @{ Authorization = 'Bearer ' + $OwnerToken }
    try {
        $account = Invoke-WeChatNativeRequest -Uri $AccountUrl -Headers $headers -TimeoutSec 6
    } catch {
        throw '无法再次确认微信登录状态，请检查本机 Native 服务。'
    }
    $state = Get-WeChatLoginState $account
    if ($account.id -cne $AccountUrl.Segments[-1]) { throw '本机 Native 返回的账号不符。' }
    if ($state -eq 'online') { Write-Host '微信已在线。'; return $true }
    if ($state -eq 'reconnecting' -and !$ForceQr) {
        Write-Host '微信正在重连，稍后可以用 -Status 查看。'
        return $false
    }
    if ($state -notin 'needs_login', 'reconnecting') {
        throw '本机 Native 返回的账号状态无法确认。'
    }
    $strategy = if ($ForceQr) { 'qr' } else { 'auto' }
    $request = @{
        computerName = $DeviceName.Trim()
        strategy = $strategy
        timeoutSeconds = 300
        qrRefresh = @{ mode = 'auto'; maxRefreshes = 3 }
    } | ConvertTo-Json -Depth 3 -Compress
    $options = @{
        Uri = "$AccountUrl/login-attempts"
        Method = 'Post'
        Headers = $headers + @{ 'Idempotency-Key' = [guid]::NewGuid().ToString() }
        Body = [Text.Encoding]::UTF8.GetBytes($request)
        TimeoutSec = 12
    }
    try {
        $attempt = Invoke-WeChatNativeRequest @options
    } catch {
        $code = Get-WeChatHttpStatus $_
        if ($code -eq 409) { throw '微信已有登录或重连任务，请等它结束后再试。' }
        if ($code -in 401, 403) { throw '本机 Native 登录权限不可用，请检查令牌设置。' }
        throw '微信未能开始登录。请检查 Native 服务和网络后再试。'
    }
    if ($attempt.id -isnot [string] -or $attempt.id -notmatch '^[A-Za-z0-9_-]{1,128}$') {
        throw 'Native 返回了无效的登录任务编号。'
    }
    $attemptUrl = "$AccountUrl/login-attempts/$($attempt.id)"
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $viewer = $null
    $lastGeneration = 0
    $lastState = ''
    $lastPoll = -2.0
    $terminal = $false
    try {
        while ($timer.Elapsed.TotalSeconds -lt 310) {
            if ($viewer -and (Test-WeChatQrWindowClosed $viewer)) {
                Write-Host '已关闭微信登录窗口。'
                return $false
            }
            if ($timer.Elapsed.TotalSeconds - $lastPoll -ge 2) {
                $lastPoll = $timer.Elapsed.TotalSeconds
                try {
                    $attempt = Invoke-WeChatNativeRequest -Uri $attemptUrl -Headers $headers -TimeoutSec 5
                } catch {
                    $code = Get-WeChatHttpStatus $_
                    if ($code -in 401, 403, 404) { throw '微信登录任务不可读，请检查本机服务后重新启动。' }
                    Start-Sleep -Milliseconds 150
                    continue
                }
                if ($attempt.state -eq 'succeeded') {
                    $terminal = $true
                    Write-Host '微信登录已确认。'
                    return $true
                }
                if ($attempt.state -in 'failed', 'rejected', 'expired', 'cancelled', 'needs_login', 'needs_review') {
                    $terminal = $true
                    if ($attempt.state -eq 'expired') { throw '微信登录二维码已失效。请重新运行启动脚本获取新码。' }
                    if ($attempt.result.code -eq 'login_lan_challenge_failed') {
                        throw '手机未能连接本机完成微信确认。请确认手机与电脑的局域网可以互通，再重新登录。'
                    }
                    throw ('微信登录结束，状态为 {0}。请查看手机提示后重新试一次。' -f $attempt.state)
                }
                $generation = 0
                $hasQr = $attempt.state -eq 'awaiting_scan' -and $attempt.result.qrAvailable -eq $true -and
                    [int]::TryParse([string]$attempt.result.qrGeneration, [ref]$generation) -and $generation -gt 0
                if ($hasQr -and $generation -ne $lastGeneration) {
                    if (!$viewer) { $viewer = New-WeChatQrWindow }
                    Set-WeChatQrWindow $viewer '正在读取最新的微信登录二维码……'
                    $null = New-Item -ItemType Directory -Path $ImageDirectory -Force
                    $qrFile = Join-Path $ImageDirectory ('login-qr-' + [guid]::NewGuid().ToString('N') + '.png')
                    $download = @{
                        Uri = ('{0}/qr?generation={1}' -f $attemptUrl, $generation)
                        Headers = $headers
                        OutFile = $qrFile
                        TimeoutSec = 5
                    }
                    try {
                        try { $null = Invoke-WeChatNativeRequest @download } catch {
                            $code = Get-WeChatHttpStatus $_
                            if ($code -notin 404, 409 -and ($code -ne 0 -or !(Test-WeChatTransportError $_))) {
                                throw '微信二维码下载失败，请稍后重试。'
                            }
                            continue
                        }
                        Set-WeChatQrWindow $viewer '用手机微信扫一扫，并按手机提示确认。' $qrFile
                        $lastGeneration = $generation
                    } finally {
                        if (Test-Path -LiteralPath $qrFile) { Remove-Item -LiteralPath $qrFile -ErrorAction SilentlyContinue }
                    }
                } elseif ($attempt.state -ne $lastState -and $attempt.state -in 'awaiting_approval', 'authenticating') {
                    if (!$viewer) { $viewer = New-WeChatQrWindow }
                    if ($attempt.state -eq 'awaiting_approval') {
                        Set-WeChatQrWindow $viewer '请在手机微信上确认登录。'
                    } else {
                        Set-WeChatQrWindow $viewer '微信正在验证，请稍等……'
                    }
                } elseif (!$hasQr -and $viewer -and $attempt.state -eq 'awaiting_scan') {
                    Set-WeChatQrWindow $viewer '二维码正在更新，请稍等……'
                    $lastGeneration = 0
                }
                $lastState = $attempt.state
            }
            Start-Sleep -Milliseconds 150
        }
        throw '等待微信确认超时。请重新运行启动脚本获取新码。'
    } finally {
        if ($viewer) { Close-WeChatQrWindow $viewer }
        if (!$terminal) {
            try {
                $null = Invoke-WeChatNativeRequest -Uri "$attemptUrl/cancel" -Method Post -Headers $headers -TimeoutSec 4
            } catch { Write-Warning '没有确认微信登录任务已取消，任务将按原期限结束。' }
        }
    }
}
