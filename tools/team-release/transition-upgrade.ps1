param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [int]$Port = 3000,
    [switch]$SavedAndIdle
)
$ErrorActionPreference = 'Stop'
if (-not $SavedAndIdle) { throw 'Save all canvases, finish Agent/generation/retrieval tasks and close canvas tabs. Then run again with -SavedAndIdle.' }
$root = (Resolve-Path -LiteralPath $InstallDir).Path
$runtime = Join-Path $root 'python\python.exe'
if (-not (Test-Path -LiteralPath $runtime) -or -not (Test-Path -LiteralPath (Join-Path $root 'main.py'))) { throw 'Not an existing Windows installation.' }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = 'amzjingfan/infinite-canvas-team'
$headers = @{ 'User-Agent' = 'Infinite-Canvas-Team-Transition' }
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest" -Headers $headers
$tag = $release.tag_name
if ($tag -notmatch '^v\d+(\.\d+){2,4}$') { throw 'Unsupported release tag.' }
$stage = Join-Path ([IO.Path]::GetTempPath()) ('Infinite-Canvas-Transition-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
$asset = 'infinite-canvas-update.zip'
$archive = Join-Path $stage $asset
Write-Host "Downloading $tag. Existing data and keys will stay in place."
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$repo/releases/download/$tag/$asset" -Headers $headers -OutFile $archive
$checksumFile = Join-Path $stage ($asset + '.sha256')
Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$repo/releases/download/$tag/$asset.sha256" -Headers $headers -OutFile $checksumFile
$expected = ((Get-Content -LiteralPath $checksumFile -Raw).Trim() -split '\s+')[0]
if ($expected -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw 'Checksum mismatch. Original installation unchanged.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    foreach ($name in @('team_update.py','team_update_worker.py','team_transition.py')) {
        $entry = $zip.GetEntry($name)
        if ($null -eq $entry) { throw "Release lacks migration component: $name" }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,(Join-Path $stage $name))
    }
} finally { $zip.Dispose() }
& $runtime -B (Join-Path $stage 'team_transition.py') --root $root --archive $archive --port $Port
if ($LASTEXITCODE -ne 0) { throw "Transition stopped. Review data\team-update\status.json; staging files retained at $stage" }
Write-Host "Upgrade complete. Open http://127.0.0.1:$Port/static/team-update.html for future updates."
Write-Host "Verified download retained at $stage; program rollback backups are in data\team-update."
