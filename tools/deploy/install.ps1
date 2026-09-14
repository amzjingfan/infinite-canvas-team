param(
    [ValidateRange(1024,65535)][int]$Port = 3000,
    [string]$TaskName = 'Infinite Canvas Background',
    [switch]$CheckOnly,
    [switch]$SkipSkill,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$deployRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This package requires Windows x64.' }
$manifestPath = Join-Path $deployRoot 'package-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) { throw 'Missing package-manifest.json. Extract the complete release ZIP first.' }
$manifest = [IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json
$manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
$receipt = Join-Path $deployRoot '.package-verified'
$verifiedBefore = (Test-Path -LiteralPath $receipt) -and ((Get-Content -LiteralPath $receipt -Raw).Trim() -eq $manifestHash)
foreach ($file in $manifest.files) {
    $target = [IO.Path]::GetFullPath((Join-Path $deployRoot $file.path))
    if (-not $target.StartsWith($deployRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid manifest path.' }
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Package file missing: $($file.path)" }
    if (-not ($verifiedBefore -and $file.mutable) -and (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $file.sha256) { throw "Package file changed: $($file.path). Do not reinstall over a used directory; extract a fresh copy." }
}
[IO.File]::WriteAllText($receipt, $manifestHash)
Write-Host 'PACKAGE_INTEGRITY_OK'
$runtime = Join-Path $deployRoot 'python/python.exe'
& $runtime -B (Join-Path $PSScriptRoot 'serve.py') --check
if ($LASTEXITCODE -ne 0) { throw 'Bundled runtime dependency check failed.' }
if ($CheckOnly) { Write-Host 'CHECK_OK'; return }

$shell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$starter = Join-Path $PSScriptRoot 'start-background.ps1'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Port {1}' -f $starter,$Port
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and (@($existing.Actions).Count -ne 1 -or $existing.Actions[0].Arguments -ne $arguments -or $existing.Actions[0].Execute -ne $shell)) {
    throw "Task '$TaskName' belongs to another installation. Nothing replaced; choose another TaskName and Port."
}
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listeners[0].OwningProcess)"
    if (-not $existing -or -not $owner.CommandLine.Contains((Join-Path $PSScriptRoot 'serve.py'))) {
        throw "Port $Port is occupied. No process was stopped. Choose another -Port."
    }
}

$privateKeys = Join-Path $deployRoot 'PRIVATE-API-KEYS.txt'
$envTarget = Join-Path $deployRoot 'API/.env'
if (Test-Path -LiteralPath $privateKeys) {
    $incoming = [IO.File]::ReadAllText($privateKeys)
    if ((Test-Path -LiteralPath $envTarget) -and -not [string]::IsNullOrWhiteSpace([IO.File]::ReadAllText($envTarget)) -and [IO.File]::ReadAllText($envTarget) -ne $incoming) {
        throw 'Existing API/.env differs. It was preserved. Use a fresh release directory or review configuration first.'
    }
    [IO.File]::WriteAllText($envTarget, $incoming, (New-Object Text.UTF8Encoding($false)))
    Write-Host 'PLATFORM_KEYS_IMPORTED (values hidden)'
}

if (-not $SkipSkill) {
    $skillName = 'gpt-image-2-style-library'
    $skillSource = Join-Path $deployRoot "bundled-skills/$skillName"
    $skillParent = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.agents/skills'
    $skillTarget = Join-Path $skillParent $skillName
    if (Test-Path -LiteralPath $skillSource) {
        if (Test-Path -LiteralPath $skillTarget) { Write-Host 'SKILL_EXISTS_PRESERVED' }
        else {
            New-Item -ItemType Directory -Path $skillParent -Force | Out-Null
            Copy-Item -LiteralPath $skillSource -Destination $skillTarget -Recurse
            Write-Host 'SKILL_INSTALLED'
        }
    }
}
if (-not $existing) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $shell -Argument $arguments -WorkingDirectory $deployRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Infinite Canvas at $deployRoot" | Out-Null
}
if (-not $listeners.Count) { Start-ScheduledTask -TaskName $TaskName }
$ready = $false
for ($attempt = 0; $attempt -lt 25; $attempt++) {
    try {
        $providers = Invoke-RestMethod "http://127.0.0.1:$Port/api/providers" -TimeoutSec 2
        $page = Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2
        if ($page.StatusCode -eq 200 -and $providers.providers) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { throw "Startup failed. Inspect $deployRoot/logs/server.stderr.log; task retained for diagnosis." }
Write-Host "DEPLOY_OK http://127.0.0.1:$Port/"
Write-Host "Task: $TaskName (starts when this Windows user logs in)"
if (Test-Path -LiteralPath $privateKeys) { Write-Host 'Platform keys imported from the private document. No generation was submitted.' }
else { Write-Host 'No private keys shipped. Configure platform keys separately; never commit them to GitHub.' }
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/" }
