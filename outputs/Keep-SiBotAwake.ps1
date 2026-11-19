# Holds a Windows system power request for one AstrBot process.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][int]$BotProcessId,
    [Parameter(Mandatory)][long]$BotStartTicks
)

$ErrorActionPreference = 'Stop'
$bot = Get-Process -Id $BotProcessId -ErrorAction SilentlyContinue
if (!$bot -or $bot.StartTime.ToUniversalTime().Ticks -ne $BotStartTicks) { return }

$identity = "Local\SiBotAwake-$BotProcessId-$BotStartTicks"
$created = $false
$owner = [Threading.Mutex]::new($true, "$identity-owner", [ref]$created)
if (!$created) {
    $owner.Dispose()
    return
}

$ready = $null
$stop = $null
$powerRequest = [IntPtr]::Zero
try {
    $stop = [Threading.EventWaitHandle]::new($false, [Threading.EventResetMode]::ManualReset, "$identity-stop")
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class SiBotPowerRequest
{
    [StructLayout(LayoutKind.Sequential)]
    private struct ReasonContext
    {
        public uint Version;
        public uint Flags;
        public IntPtr Reason;
        // The native union can also hold a larger detailed reason.
        public IntPtr Reserved1;
        public IntPtr Reserved2;
        public IntPtr Reserved3;
    }

    private const int SystemRequired = 1;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr PowerCreateRequest(ref ReasonContext context);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool PowerSetRequest(IntPtr request, int type);

    [DllImport("kernel32.dll")]
    private static extern bool PowerClearRequest(IntPtr request, int type);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll")]
    private static extern ulong GetTickCount64();

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool QueryUnbiasedInterruptTime(out ulong awakeTime);

    public static long SuspendedMilliseconds()
    {
        ulong awakeTime;
        if (!QueryUnbiasedInterruptTime(out awakeTime))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        return (long)GetTickCount64() - (long)(awakeTime / 10000);
    }

    public static IntPtr Acquire()
    {
        IntPtr reason = Marshal.StringToHGlobalUni("SiBot is running group chat and scheduled reminders");
        try
        {
            var context = new ReasonContext { Version = 0, Flags = 1, Reason = reason };
            IntPtr request = PowerCreateRequest(ref context);
            if (request == IntPtr.Zero || request == new IntPtr(-1))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if (PowerSetRequest(request, SystemRequired))
                return request;
            int error = Marshal.GetLastWin32Error();
            CloseHandle(request);
            throw new Win32Exception(error);
        }
        finally { Marshal.FreeHGlobal(reason); }
    }

    public static void Release(IntPtr request)
    {
        PowerClearRequest(request, SystemRequired);
        CloseHandle(request);
    }
}
'@
    if ($bot.HasExited -or $stop.WaitOne(0)) { return }
    $powerRequest = [SiBotPowerRequest]::Acquire()
    $suspended = [SiBotPowerRequest]::SuspendedMilliseconds()
    $ready = [Threading.EventWaitHandle]::new($true, [Threading.EventResetMode]::ManualReset, "$identity-ready")
    while (!$bot.HasExited -and !$stop.WaitOne(2000)) {
        $current = [SiBotPowerRequest]::SuspendedMilliseconds()
        if ($current - $suspended -gt 1000) {
            $null = $ready.Reset()
            [SiBotPowerRequest]::Release($powerRequest)
            $powerRequest = [IntPtr]::Zero
            $powerRequest = [SiBotPowerRequest]::Acquire()
            $null = $ready.Set()
        }
        $suspended = $current
    }
} finally {
    if ($powerRequest -ne [IntPtr]::Zero) { [SiBotPowerRequest]::Release($powerRequest) }
    if ($ready) { $null = $ready.Reset(); $ready.Dispose() }
    if ($stop) { $stop.Dispose() }
    $owner.ReleaseMutex()
    $owner.Dispose()
    $bot.Dispose()
}
