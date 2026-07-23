param(
    [string]$TaskName = "Admission Radar",
    [ValidateRange(15, 1440)]
    [int]$IntervalMinutes = 60,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..")
)
$MainScript = Join-Path $ProjectDir "main.py"
$ConfigPath = Join-Path $ProjectDir "config.json"

if (-not (Test-Path -LiteralPath $MainScript -PathType Leaf)) {
    throw "Main script not found: $MainScript"
}
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "config.json not found. Copy and edit config.example.json first."
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
        $PythonPath = $VenvPython
    }
    else {
        $PythonCommand = Get-Command python -ErrorAction Stop
        $PythonPath = $PythonCommand.Source
    }
}
$PythonPath = [System.IO.Path]::GetFullPath($PythonPath)
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python not found: $PythonPath"
}

$ActionArguments = "`"$MainScript`" --config `"$ConfigPath`""
$Action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument $ActionArguments `
    -WorkingDirectory $ProjectDir

$StartAt = (Get-Date).AddMinutes(1)
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $StartAt `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Check admission announcements and email newly published items."

Register-ScheduledTask `
    -TaskName $TaskName `
    -InputObject $Task `
    -Force | Out-Null

Write-Host "Scheduled task created: $TaskName"
Write-Host "First run: $StartAt"
Write-Host "Interval: $IntervalMinutes minutes"
Write-Host "Python: $PythonPath"
Write-Host "Project: $ProjectDir"
