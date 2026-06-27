[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Foreground,
    [int]$PollIntervalSeconds = 30
)

 $ErrorActionPreference = "Stop"
 $BotFolder = "C:\Users\Никита\Documents\Polymarket"
 $BotScript = "run.py"
 $TaskName = "PolymarketBotWatcher"
 $LogFile = "C:\Users\Никита\Documents\Polymarket\watcher.log"

function Write-Log { param($msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
    if ($Foreground) { Write-Host $line -ForegroundColor Cyan }
}
function Write-LogOk { param($msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [OK] $msg"
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
    if ($Foreground) { Write-Host $line -ForegroundColor Green }
}
function Write-LogWarn { param($msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [!] $msg"
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
    if ($Foreground) { Write-Host $line -ForegroundColor Yellow }
}

function Send-TelegramNotification {
    param([string]$Message)
    $envFile = Join-Path $BotFolder ".env"
    if (-not (Test-Path $envFile)) { return }
    $token = $null; $userId = $null
    Get-Content $envFile | ForEach-Object {
        if ($_ -match "^TELEGRAM_BOT_TOKEN=(.+)$") { $token = $Matches[1].Trim() }
        if ($_ -match "^TELEGRAM_ALLOWED_USER_ID=(.+)$") { $userId = $Matches[1].Trim() }
    }
    if (-not $token -or -not $userId) { return }
    try {
        $body = @{ chat_id = $userId; text = $Message } | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/sendMessage" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 10 | Out-Null
    } catch { Write-LogWarn "Telegram notification failed: $($_.Exception.Message)" }
}

function Stop-RunningBot {
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*$BotScript*" -or $_.CommandLine -like "*Polymarket*" }
    if ($procs) {
        $procs | ForEach-Object { Write-Log "Stopping PID $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
        Write-LogOk "Bot stopped."
    }
}

function Start-Bot {
    $venvPython = Join-Path $BotFolder ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) { $venvPython = "python" }
    $botScriptPath = Join-Path $BotFolder $BotScript
    if (-not (Test-Path $botScriptPath)) { Write-LogWarn "Bot script not found: $botScriptPath"; return }
    Start-Process -FilePath $venvPython -ArgumentList $botScriptPath -WorkingDirectory $BotFolder -WindowStyle Hidden
    Write-LogOk "Bot started."
}

function Invoke-GitPull {
    Push-Location $BotFolder
    try {
        $output = & git pull --ff-only 2>&1
        $exitCode = $LASTEXITCODE
        $outputText = ($output | Out-String).Trim()
        if ($exitCode -ne 0) { Write-LogWarn "git pull failed: $outputText"; return @{ Success = $false; Changed = $false; Message = $outputText } }
        $changed = -not ($outputText -match "Already up to date")
        return @{ Success = $true; Changed = $changed; Message = $outputText }
    } finally { Pop-Location }
}

function Get-LatestCommitInfo {
    Push-Location $BotFolder
    try {
        $hash = (& git rev-parse --short HEAD 2>&1 | Out-String).Trim()
        $msg = (& git log -1 --pretty=%s 2>&1 | Out-String).Trim()
        $author = (& git log -1 --pretty=%an 2>&1 | Out-String).Trim()
        return @{ Hash = $hash; Message = $msg; Author = $author }
    } finally { Pop-Location }
}

if ($Install) {
    Write-Host "Installing PolymarketBotWatcher as a Scheduled Task..." -ForegroundColor Cyan
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" -PollIntervalSeconds $PollIntervalSeconds"
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Days 36500)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User $user -RunLevel Limited | Out-Null
    Write-Host "Installed. Starting task..." -ForegroundColor Green
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Watcher is running in the background." -ForegroundColor Green
    Write-Host ""
    Write-Host "Logs: $LogFile" -ForegroundColor Gray
    Write-Host "Stop:  Stop-ScheduledTask -TaskName $TaskName" -ForegroundColor Gray
    Write-Host "Uninstall: .\bot-watcher.ps1 -Uninstall" -ForegroundColor Gray
    exit 0
}

if ($Uninstall) {
    Write-Host "Uninstalling PolymarketBotWatcher..." -ForegroundColor Cyan
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Uninstalled." -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $BotFolder)) { Write-LogWarn "Bot folder not found: $BotFolder"; exit 1 }
 $gitExe = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitExe) { Write-LogWarn "git not installed. Install: https://git-scm.com/download/win"; exit 1 }

Write-Log "Watcher started. Poll interval: ${PollIntervalSeconds}s."

while ($true) {
    try {
        $result = Invoke-GitPull
        if ($result.Success -and $result.Changed) {
            Write-Log "Changes detected. Restarting bot."
            $commit = Get-LatestCommitInfo
            Stop-RunningBot
            $reqPath = Join-Path $BotFolder "requirements.txt"
            if (Test-Path $reqPath) {
                Write-Log "Installing dependencies..."
                $venvPython = Join-Path $BotFolder ".venv\Scripts\python.exe"
                if (Test-Path $venvPython) { & $venvPython -m pip install -q -r $reqPath 2>&1 | Out-Null }
            }
            Start-Bot
            $notification = "Bot updated to commit $($commit.Hash)`n$($commit.Message)`nby $($commit.Author)"
            Send-TelegramNotification -Message $notification
            Write-LogOk "Update notification sent to Telegram."
        }
    } catch { Write-LogWarn "Watcher loop error: $($_.Exception.Message)" }
    Start-Sleep -Seconds $PollIntervalSeconds
}
