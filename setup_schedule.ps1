[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pipeline = Join-Path $projectRoot "scripts\daily_pipeline.py"
$database = Join-Path $projectRoot "data\tw_stock.db"
$envFile = Join-Path $projectRoot ".env"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python environment not found: $python. Run setup_windows.bat first."
}
if (-not (Test-Path -LiteralPath $pipeline -PathType Leaf)) {
    throw "Daily pipeline not found: $pipeline. Run this script from the project folder."
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw ".env not found. Create it from .env.example and configure credentials before setting up the schedule."
}

$schedules = @(
    @{ Suffix = "1000"; Time = "10:00"; Flags = "" },
    @{ Suffix = "1100"; Time = "11:00"; Flags = "" },
    @{ Suffix = "1200"; Time = "12:00"; Flags = "" },
    @{ Suffix = "1300"; Time = "13:00"; Flags = "" },
    @{ Suffix = "1330"; Time = "13:30"; Flags = "" },
    @{ Suffix = "1430"; Time = "14:30"; Flags = "" },
    @{ Suffix = "1700"; Time = "17:00"; Flags = "--dry-run --chip-refresh" },
    @{ Suffix = "2100"; Time = "21:00"; Flags = "--dry-run --skip-tpex" }
)

$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$failedTasks = [System.Collections.Generic.List[string]]::new()

foreach ($schedule in $schedules) {
    $taskName = "tw_stock_pipeline_$($schedule.Suffix)"
    $time = [datetime]::ParseExact(
        $schedule.Time,
        "HH:mm",
        [Globalization.CultureInfo]::InvariantCulture
    )
    $arguments = '"{0}" --local-db "{1}" {2}' -f $pipeline, $database, $schedule.Flags
    $action = New-ScheduledTaskAction `
        -Execute $python `
        -Argument $arguments `
        -WorkingDirectory $projectRoot
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $time

    if ($WhatIfPreference) {
        Write-Host "[WhatIf] Would create or update $taskName ($($schedule.Time))"
        continue
    }

    try {
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal `
            -Description "tw_stock daily pipeline at $($schedule.Time) on weekdays" `
            -Force | Out-Null
        Write-Host "Created or updated $taskName ($($schedule.Time))"
    }
    catch {
        $failedTasks.Add("$taskName`: $($_.Exception.Message)")
        Write-Error "Failed to create or update $taskName`: $($_.Exception.Message)" -ErrorAction Continue
    }
}

if ($failedTasks.Count -gt 0) {
    throw "Some scheduled tasks could not be created:`n$($failedTasks -join "`n")"
}

Write-Host ""
if ($WhatIfPreference) {
    Write-Host "Preview complete. No scheduled tasks were changed."
}
else {
    Write-Host "All 8 weekday tasks are installed for the current Windows user."
    Write-Host "FINMIND_API_TOKEN must be configured in .env before the tasks run."
    Write-Host "Tasks run only while this user is logged on; missed runs start when the task becomes available."
}
