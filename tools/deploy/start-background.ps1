param([ValidateRange(1024,65535)][int]$Port = 3000)
$ErrorActionPreference = 'Stop'
$deployRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$logDir = Join-Path $deployRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runtime = Join-Path $deployRoot 'python/python.exe'
$entry = Join-Path $PSScriptRoot 'serve.py'
# Invoke directly so Task Scheduler owns the process tree. Start-Process can
# detach Python and leave the port occupied after stopping the scheduled task.
$ErrorActionPreference = 'Continue'
& $runtime -B -u $entry --port $Port 1>> (Join-Path $logDir 'server.stdout.log') 2>> (Join-Path $logDir 'server.stderr.log')
exit $LASTEXITCODE
