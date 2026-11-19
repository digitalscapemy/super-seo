# install.ps1 - copy this skill into the Claude Code skills folder (re-run after editing the source).
#   .\install.ps1                 install / update the skill (+ extensions, hooks, agents)
#   .\install.ps1 -EnableHook     also register the JSON-LD PostToolUse hook in settings.json
#   .\install.ps1 -DisableHook    remove only that hook entry from settings.json
# Honours $env:CLAUDE_CONFIG_DIR (default: %USERPROFILE%\.claude). Windows PowerShell 5.1 compatible.
param(
    [switch]$EnableHook,
    [switch]$DisableHook
)
$ErrorActionPreference = "Stop"
if ($EnableHook -and $DisableHook) { throw "Use either -EnableHook or -DisableHook, not both." }

$src = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($env:CLAUDE_CONFIG_DIR) { $configDir = $env:CLAUDE_CONFIG_DIR } else { $configDir = Join-Path $env:USERPROFILE ".claude" }
$dest = Join-Path $configDir "skills\super-seo"
$inPlace = ((Resolve-Path $src).Path.TrimEnd('\') -ieq ([IO.Path]::GetFullPath($dest)).TrimEnd('\'))

# ---------------------------------------------------------------- skill files
if ($inPlace) {
    Write-Host "Already installed in place ($dest). Update with: git pull"
} else {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    foreach ($item in "SKILL.md", "README.md", "references", "scripts", "templates", "extensions", "hooks") {
        $from = Join-Path $src $item
        if (-not (Test-Path $from)) { continue }
        $target = Join-Path $dest $item
        if (Test-Path $target) { Remove-Item -Recurse -Force -Confirm:$false $target }
        Copy-Item -Recurse -Force $from $target
    }
    Get-ChildItem -Recurse -Directory -Filter "__pycache__" $dest | Remove-Item -Recurse -Force -Confirm:$false
    Write-Host "Installed super-seo -> $dest"
}

# ---------------------------------------------------------------- agents (only super-seo-*.md are touched)
$agentsSrc = Join-Path $src "agents"
if (Test-Path $agentsSrc) {
    $agentFiles = @(Get-ChildItem -Path $agentsSrc -Filter "super-seo-*.md" -File)
    if ($agentFiles.Count -gt 0) {
        $agentsDest = Join-Path $configDir "agents"
        New-Item -ItemType Directory -Force -Path $agentsDest | Out-Null
        foreach ($f in $agentFiles) { Copy-Item -Force $f.FullName (Join-Path $agentsDest $f.Name) }
        Write-Host "Installed $($agentFiles.Count) agent(s) -> $agentsDest"
    }
}

# ---------------------------------------------------------------- hook registration
if (-not ($EnableHook -or $DisableHook)) { exit 0 }

$hookPath = (Join-Path $dest "hooks\validate_jsonld.py") -replace '\\', '/'
$hookCommand = "python `"$hookPath`""
$matcher = "Edit|Write|MultiEdit"
$settingsPath = Join-Path $configDir "settings.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Test-OurHook($cmd) {
    if ($null -eq $cmd) { return $false }
    return ([string]$cmd -like "*super-seo*validate_jsonld.py*")
}

function Save-Settings($obj) {
    $json = ConvertTo-Json -InputObject $obj -Depth 100
    if (Test-Path $settingsPath) {
        $backup = "$settingsPath.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        Copy-Item -Force $settingsPath $backup
        Write-Host "Backed up settings.json -> $backup"
    }
    [IO.File]::WriteAllText($settingsPath, $json + "`n", $utf8NoBom)
}

$settings = $null
if (Test-Path $settingsPath) {
    $raw = [IO.File]::ReadAllText($settingsPath, [Text.Encoding]::UTF8)
    if ($raw.Trim().Length -gt 0) {
        try { $settings = $raw | ConvertFrom-Json }
        catch { throw "settings.json is not valid JSON; fix it first (nothing was changed). $($_.Exception.Message)" }
    }
}
if ($null -eq $settings) { $settings = New-Object PSObject }

if ($EnableHook) {
    $hookFile = Join-Path $dest "hooks\validate_jsonld.py"
    if (-not (Test-Path $hookFile)) { throw "Hook script not found at $hookFile; install the skill first." }
    if ($null -eq $settings.PSObject.Properties["hooks"] -or $null -eq $settings.hooks) {
        $settings | Add-Member -NotePropertyName "hooks" -NotePropertyValue (New-Object PSObject) -Force
    }
    $groups = @()
    if ($null -ne $settings.hooks.PSObject.Properties["PostToolUse"] -and $null -ne $settings.hooks.PostToolUse) {
        $groups = @($settings.hooks.PostToolUse)
    }
    $present = $false
    foreach ($g in $groups) {
        foreach ($h in @($g.hooks)) { if ($null -ne $h -and $h.command -eq $hookCommand) { $present = $true } }
    }
    if ($present) {
        Write-Host "JSON-LD hook already enabled in $settingsPath"
        exit 0
    }
    $entry = New-Object PSObject -Property @{ type = "command"; command = $hookCommand }
    $group = New-Object PSObject
    $group | Add-Member -NotePropertyName "matcher" -NotePropertyValue $matcher
    $group | Add-Member -NotePropertyName "hooks" -NotePropertyValue @($entry)
    $groups = @($groups) + @($group)
    $settings.hooks | Add-Member -NotePropertyName "PostToolUse" -NotePropertyValue $groups -Force
    Save-Settings $settings
    Write-Host "Enabled JSON-LD hook in $settingsPath"
    Write-Host "  command: $hookCommand"
    exit 0
}

if ($DisableHook) {
    $removed = 0
    $hasPost = ($null -ne $settings.PSObject.Properties["hooks"] -and $null -ne $settings.hooks -and
                $null -ne $settings.hooks.PSObject.Properties["PostToolUse"] -and $null -ne $settings.hooks.PostToolUse)
    if ($hasPost) {
        $kept = @()
        foreach ($g in @($settings.hooks.PostToolUse)) {
            $inner = @($g.hooks)
            $keepInner = @($inner | Where-Object { -not (Test-OurHook $_.command) })
            $removed += ($inner.Count - $keepInner.Count)
            if ($keepInner.Count -eq $inner.Count) { $kept += ,$g; continue }
            if ($keepInner.Count -gt 0) {
                $g | Add-Member -NotePropertyName "hooks" -NotePropertyValue $keepInner -Force
                $kept += ,$g
            }
        }
        if ($kept.Count -gt 0) {
            $settings.hooks | Add-Member -NotePropertyName "PostToolUse" -NotePropertyValue $kept -Force
        } else {
            $settings.hooks.PSObject.Properties.Remove("PostToolUse")
            if (@($settings.hooks.PSObject.Properties).Count -eq 0) { $settings.PSObject.Properties.Remove("hooks") }
        }
    }
    if ($removed -gt 0) {
        Save-Settings $settings
        Write-Host "Removed $removed super-seo JSON-LD hook entr$(if ($removed -eq 1) { 'y' } else { 'ies' }) from $settingsPath"
    } else {
        Write-Host "No super-seo JSON-LD hook found in $settingsPath (nothing changed)"
    }
    exit 0
}
