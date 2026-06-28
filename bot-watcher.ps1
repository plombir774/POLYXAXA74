#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Foreground,
    [int]$PollIntervalSeconds = 30,
    [int]$HealthCheckIntervalSeconds = 300
)

# NOTE: $ErrorActionPreference is intentionally NOT "Stop" here.
# When it is "Stop", git's normal stderr output (e.g. the "From https://github.com/..."
# fetch header, or "warning: LF will be replaced by CRLF") is turned into a
# terminating error by the "2>&1" redirection, which aborted the loop before the
# exit code was ever checked. We drive git via $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"

# Build paths from the user profile so we never embed Cyrillic literals:
# Windows PowerShell 5.1 reads non-BOM scripts as ANSI and mangles them.
$BotFolder = Join-Path $env:USERPROFILE "Documents\Polymarket"
$BotScript = "run.py"
$TaskName  = "PolymarketBotWatcher"
$LogFile   = Join-Path $BotFolder "watcher.log"

# Tracked branch to follow on the remote. Refined at startup from the local HEAD.
$Branch = "main"

function Write-Log {
    param($msg)
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
    if ($Foreground) { Write-Host $line -ForegroundColor Cyan }
}
function Write-LogOk {
    param($msg)
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [OK] $msg"
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
    if ($Foreground) { Write-Host $line -ForegroundColor Green }
}
function Write-LogWarn {
    param($msg)
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [!] $msg"
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
    if ($Foreground) { Write-Host $line -ForegroundColor Yellow }
}

function Send-TelegramNotification {
    param([string]$Message)
    $envFile = Join-Path $BotFolder ".env"
    if (-not (Test-Path -LiteralPath $envFile)) { return }
    $token  = $null
    $userId = $null
    Get-Content -LiteralPath $envFile | ForEach-Object {
        if ($_ -match "^TELEGRAM_BOT_TOKEN=(.+)$")       { $token  = $Matches[1].Trim() }
        if ($_ -match "^TELEGRAM_ALLOWED_USER_ID=(.+)$") { $userId = $Matches[1].Trim() }
    }
    if (-not $token -or -not $userId) { return }
    try {
        $body = @{ chat_id = $userId; text = $Message } | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/sendMessage" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 10 | Out-Null
    } catch {
        Write-LogWarn "Telegram notification failed: $($_.Exception.Message)"
    }
}

function Stop-RunningBot {
    # Match ONLY python processes that run the bot: the command line must contain
    # BOTH the bot script (run.py) and the Polymarket folder, so unrelated python
    # processes are never touched.
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like "*$BotScript*" -and
            $_.CommandLine -like "*Polymarket*"
        }
    if ($procs) {
        foreach ($p in $procs) {
            Write-Log "Stopping PID $($p.ProcessId)"
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
        Write-LogOk "Bot stopped."
    }
}

function Start-Bot {
    $venvPython = Join-Path $BotFolder ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) { $venvPython = "python" }
    $botScriptPath = Join-Path $BotFolder $BotScript
    if (-not (Test-Path -LiteralPath $botScriptPath)) {
        Write-LogWarn "Bot script not found: $botScriptPath"
        return
    }
    # Minimized (not Hidden) so the process stays visible in the taskbar if it
    # fails silently — easier to notice and debug.
    Start-Process -FilePath $venvPython -ArgumentList $botScriptPath -WorkingDirectory $BotFolder -WindowStyle Minimized
    Write-LogOk "Bot started."
}

function Get-BotPid {
    # Returns the bot's PID if alive, $null otherwise.
    $proc = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like "*$BotScript*" -and
            $_.CommandLine -like "*Polymarket*"
        } |
        Select-Object -First 1
    if ($proc) { return $proc.ProcessId }
    return $null
}

function Invoke-GitPull {
    # We deliberately avoid bare `git pull`. When the upstream is ambiguous it
    # fails with "fatal: Cannot fast-forward to multiple branches". Instead we
    # fetch a single explicit refspec and fast-forward to FETCH_HEAD — unambiguous.
    Push-Location $BotFolder
    try {
        # 1. Stash local changes (including untracked) so the merge can fast-forward.
        $stashOut = & git stash push -u -m "watcher-auto-stash" 2>&1
        $stashOk  = $LASTEXITCODE -eq 0
        $stashed  = $stashOk -and (($stashOut | Out-String) -notmatch "No local changes to save")
        if (-not $stashOk) { Write-LogWarn "git stash failed; continuing to fetch anyway." }

        # 2. Fetch the tracked branch explicitly.
        $null = & git fetch origin $Branch 2>&1
        $fetchOk = $LASTEXITCODE -eq 0

        # 3. Fast-forward to what we just fetched.
        $mergeOut = & git merge --ff-only FETCH_HEAD 2>&1
        $mergeOk  = $LASTEXITCODE -eq 0
        $outText  = ($mergeOut | Out-String).Trim()

        if (-not $fetchOk -or -not $mergeOk) {
            # 4. Last resort: hard reset to the remote branch, then drop any stash.
            Write-LogWarn "fetch/merge failed (fetch=$fetchOk merge=$mergeOk). Hard resetting to origin/$Branch."
            & git fetch origin $Branch 2>&1 | Out-Null
            & git reset --hard "origin/$Branch" 2>&1 | Out-Null
            if ($stashed) { & git stash drop 2>&1 | Out-Null }
            return @{ Success = $true; Changed = $true; Message = "reset --hard origin/$Branch" }
        }

        # 5. Merge succeeded. Drop the auto-stash if one was created — we don't
        # want to keep untracked runtime junk (__pycache__, *.db, .pytest_cache).
        if ($stashed) { & git stash drop 2>&1 | Out-Null }

        $changed = -not ($outText -match "Already up to date" -or $outText -match "Already up-to-date")
        return @{ Success = $true; Changed = $changed; Message = $outText }
    } finally {
        Pop-Location
    }
}

function Get-LatestCommitInfo {
    Push-Location $BotFolder
    try {
        $hash   = (& git rev-parse --short HEAD 2>&1 | Out-String).Trim()
        $msg    = (& git log -1 --pretty=%s  2>&1 | Out-String).Trim()
        $author = (& git log -1 --pretty=%an 2>&1 | Out-String).Trim()
        return @{ Hash = $hash; Message = $msg; Author = $author }
    } finally {
        Pop-Location
    }
}

# ---------------------------------------------------------------- Install/Uninstall
if ($Install) {
    Write-Host "Installing PolymarketBotWatcher as a Scheduled Task..." -ForegroundColor Cyan
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    $user   = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" -PollIntervalSeconds $PollIntervalSeconds -HealthCheckIntervalSeconds $HealthCheckIntervalSeconds"
    $trigger  = New-ScheduledTaskTrigger -AtStartup
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

# ---------------------------------------------------------------- Pre-flight checks
if (-not (Test-Path -LiteralPath $BotFolder)) {
    Write-LogWarn "Bot folder not found: $BotFolder"
    exit 1
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-LogWarn "git not installed. Install: https://git-scm.com/download/win"
    exit 1
}

# Detect the current branch so we fetch/merge the right refspec.
Push-Location $BotFolder
try {
    $detected = (& git rev-parse --abbrev-ref HEAD 2>&1 | Out-String).Trim()
    if ($detected -and $detected -ne "HEAD") { $Branch = $detected }
} finally { Pop-Location }

Write-Log "Watcher started. Branch: $Branch. Poll: ${PollIntervalSeconds}s, health check every ${HealthCheckIntervalSeconds}s."

# The health check only restarts the bot if WE expect it to be running, so a
# manual stop isn't immediately undone. Seed it from whether the bot is up now.
$botExpected = ($null -ne (Get-BotPid))
$lastHealthCheck = (Get-Date)

# ---------------------------------------------------------------- Main loop
while ($true) {
    try {
        # --- 1. Pull + restart-on-change ---
        $result = Invoke-GitPull
        if ($result.Success -and $result.Changed) {
            Write-Log "Changes detected. Restarting bot."
            $commit = Get-LatestCommitInfo
            Stop-RunningBot
            $reqPath = Join-Path $BotFolder "requirements.txt"
            if (Test-Path -LiteralPath $reqPath) {
                Write-Log "Installing dependencies..."
                $venvPython = Join-Path $BotFolder ".venv\Scripts\python.exe"
                if (Test-Path -LiteralPath $venvPython) {
                    & $venvPython -m pip install -q -r $reqPath 2>&1 | Out-Null
                }
            }
            Start-Bot
            Start-Sleep -Seconds 5
            $alivePid = Get-BotPid
            if ($alivePid) {
                $botExpected = $true
                Write-LogOk "Bot is running after update (PID $alivePid)."
            } else {
                $botExpected = $false
                Write-LogWarn "Bot failed to stay alive 5s after restart!"
                Send-TelegramNotification -Message ("⚠️ Polymarket bot failed to start after update to $($commit.Hash)`n$($commit.Message)`nby $($commit.Author)")
            }
            $notification = "Bot updated to commit $($commit.Hash)`n$($commit.Message)`nby $($commit.Author)"
            Send-TelegramNotification -Message $notification
            Write-LogOk "Update notification sent to Telegram."
        }
    } catch {
        Write-LogWarn "Watcher loop error: $($_.Exception.Message)"
    }

    # --- 2. Health check on a separate, slower cadence ---
    $now = Get-Date
    if (($now - $lastHealthCheck).TotalSeconds -ge $HealthCheckIntervalSeconds) {
        $lastHealthCheck = $now
        try {
            if ($botExpected -and ($null -eq (Get-BotPid))) {
                Write-LogWarn "Health check: bot is down. Restarting."
                Start-Bot
                Start-Sleep -Seconds 5
                if ($null -ne (Get-BotPid)) {
                    $botExpected = $true
                    Write-LogOk "Health check: bot recovered."
                    Send-TelegramNotification -Message "♻️ Polymarket bot was down and has been restarted by the watcher."
                } else {
                    $botExpected = $false
                    Write-LogWarn "Health check: bot did NOT recover after restart!"
                }
            }
        } catch {
            Write-LogWarn "Health check error: $($_.Exception.Message)"
        }
    }

    Start-Sleep -Seconds $PollIntervalSeconds
}
