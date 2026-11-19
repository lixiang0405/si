# NapCat treats the WebUI token and the QQ account as separate logins.
function Invoke-SiNapCatPost {
    param(
        [System.Net.Http.HttpClient]$Client,
        [uri]$Uri,
        [string]$Json = '{}',
        [string]$Credential
    )

    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, $Uri)
    try {
        $request.Content = [System.Net.Http.StringContent]::new($Json, [Text.Encoding]::UTF8, 'application/json')
        if ($Credential) {
            $request.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Credential)
        }
        $response = $Client.SendAsync($request).GetAwaiter().GetResult()
        try {
            if (!$response.IsSuccessStatusCode) { return }
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            return ($body | ConvertFrom-Json -ErrorAction Stop)
        } finally {
            $response.Dispose()
        }
    } finally {
        $request.Dispose()
    }
}

function Get-QQLoginState {
    param($NapConfig, [ValidateRange(1, 15)][int]$TimeoutSeconds = 4)

    $result = [pscustomobject]@{ State = 'unavailable'; Phase = '' }
    $port = 0
    if ($null -eq $NapConfig -or ![int]::TryParse([string]$NapConfig.port, [ref]$port) -or
        $port -lt 1 -or $port -gt 65535 -or !$NapConfig.token) { return $result }

    $client = $null
    $sha = $null
    try {
        Add-Type -AssemblyName System.Net.Http -ErrorAction Stop
        $handler = [System.Net.Http.HttpClientHandler]::new()
        $handler.UseProxy = $false
        $handler.AllowAutoRedirect = $false
        $client = [System.Net.Http.HttpClient]::new($handler)
        $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSeconds)
        $sha = [Security.Cryptography.SHA256]::Create()
        $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(([string]$NapConfig.token + '.napcat')))).Replace('-', '').ToLowerInvariant()
        $api = "http://127.0.0.1:$port/api"
        $auth = Invoke-SiNapCatPost -Client $client -Uri "$api/auth/login" -Json (@{ hash = $hash } | ConvertTo-Json -Compress)
        $credential = [string]$auth.data.Credential
        if (!$credential) { return $result }

        $status = (Invoke-SiNapCatPost -Client $client -Uri "$api/QQLogin/CheckLoginStatus" -Credential $credential).data
        if ($null -eq $status) { return $result }
        $phase = [string]$status.loginPhase
        if ($phase -in 'ready', 'offline', 'waiting_qrcode', 'generating_qrcode', 'qrcode_scanned', 'initializing', 'reconnecting') {
            $result.Phase = $phase
        }
        if ($status.isLogin -eq $true -and $status.coreReady -eq $true -and $status.isOffline -ne $true) {
            $result.State = 'online'
        } elseif ($status.isOffline -eq $true -or $phase -eq 'offline' -or ($phase -eq 'waiting_qrcode' -and ($status.qrcodeurl -or $status.loginError))) {
            $result.State = 'needs_login'
        } elseif ($phase -in 'waiting_qrcode', 'generating_qrcode', 'qrcode_scanned', 'initializing', 'reconnecting' -or $status.qrLoginAccepted -eq $true) {
            $result.State = 'starting'
        }
    } catch {
        # Report only the state. Some exceptions may contain request or response data.
    } finally {
        if ($client) { $client.Dispose() }
        if ($sha) { $sha.Dispose() }
    }
    return $result
}

function Open-QQLogin {
    param([ValidateRange(1, 65535)][int]$NapPort)

    try {
        Start-Process -FilePath "http://127.0.0.1:$NapPort/webui/qq_login" -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}
