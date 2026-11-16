# Run in Windows PowerShell 5.1+ from a dedicated clone. No passwords/tokens are stored.
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE). Fix the error and rerun setup." }
}
function Read-Folder([string]$Label) {
    $Value = (Read-Host $Label).Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $Value -PathType Container)) { throw "Folder does not exist: $Value" }
    return (Resolve-Path -LiteralPath $Value).Path
}
Invoke-Checked 'git' @('--version')
Invoke-Checked 'git' @('credential-manager', 'configure')
Invoke-Checked 'git' @('-C', $Repo, 'fetch', 'origin')
$Branch = & git -C $Repo symbolic-ref --short HEAD
if ($LASTEXITCODE -ne 0) { throw 'Checkout a branch that tracks origin first.' }
Invoke-Checked 'git' @('-C', $Repo, 'push', '--dry-run', 'origin', "HEAD:refs/heads/$Branch")
$Raw = Read-Folder 'Raw folder (full Windows path)'
$Processed = Read-Folder 'Processed folder (full Windows path)'
if ($Raw -eq $Processed) { throw 'Select two different folders.' }
foreach ($Source in @($Raw, $Processed)) {
    if ($Source -eq $Repo -or $Source.StartsWith($Repo + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source folders must be outside this dedicated clone.'
    }
}
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { Invoke-Checked 'py' @('-3', '-m', 'venv', (Join-Path $Repo '.venv')) }
Invoke-Checked $Python @('-m', 'pip', 'install', '-e', $Repo)
$Hasher = [System.Security.Cryptography.SHA256]::Create()
$Id = ([BitConverter]::ToString($Hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($Repo.ToLowerInvariant())))).Replace('-', '').Substring(0, 12)
$Settings = Join-Path $env:LOCALAPPDATA "FTIR-Sync\$Id"
New-Item -ItemType Directory -Force -Path $Settings | Out-Null
$Config = Join-Path $Settings 'config.json'
if (Test-Path -LiteralPath $Config) {
    Copy-Item -LiteralPath $Config -Destination ($Config + '.backup-' + (Get-Date -Format 'yyyyMMddHHmmss'))
}
@{repo=$Repo; raw_folder=$Raw; processed_folder=$Processed; stability_seconds=5} |
    ConvertTo-Json | Set-Content -LiteralPath $Config -Encoding UTF8
Write-Host 'Running initial sync. Complete stable file pairs may be committed and pushed.'
Invoke-Checked $Python @('-m', 'ftir_cutoff.sync', '--config', $Config)
$TaskName = "FTIR-Sync-$Id"
$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m ftir_cutoff.sync --config `"$Config`"" -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2)
$Options = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$Task = New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Options -Principal $Principal
Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
Write-Host "Enabled: $TaskName (every 2 minutes while logged in)"
Write-Host "Config: $Config"
Write-Host "Log: $(Join-Path $Settings 'sync.log')"
Write-Host "Stop: Disable-ScheduledTask -TaskName '$TaskName'"
Write-Host "Resume: Enable-ScheduledTask -TaskName '$TaskName'"
Write-Host "Remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
