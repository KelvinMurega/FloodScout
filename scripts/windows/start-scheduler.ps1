# Starts Docker Desktop if it is not running, then brings up the database and
# the `scheduler` service, which refreshes the scores straight away and
# catches up on any days the computer was off (see refresh_scores).
#
# Run by the "FloodScout daily refresh" scheduled task that
# install-scheduler-task.ps1 registers. Safe to run by hand, and to run when
# everything is already up: `docker compose up -d` leaves running services alone.

# Not "Stop": Windows PowerShell turns anything docker writes to stderr
# (including progress) into an error. Exit codes are checked instead.
$ErrorActionPreference = "Continue"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$log = Join-Path $repo "scheduler-task.log"

function Write-Log($message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $message" | Out-File -FilePath $log -Append -Encoding utf8
}

function Test-Docker {
    docker info --format "{{.ServerVersion}}" *> $null
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Docker)) {
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) {
        Write-Log "Docker Desktop not found at $desktop"
        exit 1
    }
    Write-Log "Starting Docker Desktop"
    Start-Process -FilePath $desktop
    # The engine takes a minute or two after a cold boot.
    $deadline = (Get-Date).AddMinutes(5)
    while (-not (Test-Docker)) {
        if ((Get-Date) -gt $deadline) {
            Write-Log "Docker did not start within 5 minutes"
            exit 1
        }
        Start-Sleep -Seconds 10
    }
}

Set-Location $repo
# Not `web`: the scheduler needs only the database, and leaving the web
# service to whoever runs the dashboard avoids fighting over its port.
cmd /c "docker compose up -d db scheduler >> `"$log`" 2>&1"
if ($LASTEXITCODE -ne 0) {
    Write-Log "docker compose up failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}
Write-Log "Scheduler is up"
