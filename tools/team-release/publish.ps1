param([Parameter(Mandatory=$true)][string]$ReleaseDirectory,[Parameter(Mandatory=$true)][string]$Tag)
$ErrorActionPreference='Stop'
$releaseRoot=(Resolve-Path -LiteralPath $ReleaseDirectory).Path
$source=Join-Path $releaseRoot 'source'
$version=[IO.File]::ReadAllText((Join-Path $source 'VERSION')).Trim()
if ($Tag -ne "v$version") { throw 'Tag must match VERSION' }
if (-not (Test-Path -LiteralPath (Join-Path $source '.git'))) { throw 'Initialize and review the clean source repository before publishing.' }
$remote=git -C $source remote get-url origin
if ($remote -notmatch 'github.com[:/]amzjingfan/infinite-canvas-team(?:\.git)?$') { throw 'Unexpected publication repository' }
git -C $source push origin main
if ($LASTEXITCODE -ne 0) { throw 'Source push failed' }
$assets=@(Get-ChildItem -LiteralPath $releaseRoot -File | Where-Object { $_.Name -like '*.zip' -or $_.Name -like '*.sha256' } | ForEach-Object FullName)
gh release create $Tag @assets --repo amzjingfan/infinite-canvas-team --target main --title "Infinite Canvas $version" --notes "Team release. Program-only updates preserve local data, media and keys. Full installer is Windows x64 and contains no credentials."
if ($LASTEXITCODE -ne 0) { throw 'Release publication failed' }
