# install.ps1 - copy this skill into the Claude Code skills folder (re-run after editing the source).
$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$configDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE ".claude" }
$dest = Join-Path $configDir "skills\super-seo"
if ((Resolve-Path $src).Path.TrimEnd('\') -ieq ([IO.Path]::GetFullPath($dest)).TrimEnd('\')) {
    Write-Host "Already installed in place ($dest). Update with: git pull"; exit 0
}
New-Item -ItemType Directory -Force -Path $dest | Out-Null
foreach ($item in "SKILL.md", "README.md", "references", "scripts", "templates") {
    $target = Join-Path $dest $item
    if (Test-Path $target) { Remove-Item -Recurse -Force -Confirm:$false $target }
    Copy-Item -Recurse -Force (Join-Path $src $item) $target
}
Get-ChildItem -Recurse -Directory -Filter "__pycache__" $dest | Remove-Item -Recurse -Force -Confirm:$false
Write-Host "Installed super-seo -> $dest"
