[CmdletBinding()]
param(
    [Parameter()]
    [string]$RepositoryRoot
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

$required = @(
    "README.md",
    "RESTORE.md",
    "AGENTS.md",
    ".env.example",
    "identity\AXEL.md",
    "identity\personality.md",
    "identity\operating-principles.md",
    "identity\workflows.md",
    "memory\basic-memory\axel",
    "memory\basic-memory\main",
    "memory\basic-memory\axel-project-knowledge",
    "opencode\config\opencode.json.template",
    "opencode\commands\compound.md",
    "tools\mcp\mcporter.json",
    "runtimes\paseo\config.template.json",
    "runtimes\codex\config.toml.template",
    "manifests\FILES.md",
    "manifests\VERSIONS.md",
    "manifests\SECRETS_REQUIRED.md",
    "scripts\bootstrap\bootstrap.ps1",
    "scripts\verify\secret-scan.ps1"
)

$missing = @()
foreach ($relativePath in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $RepositoryRoot $relativePath))) {
        $missing += $relativePath
    }
}

$skillCount = @(Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot "opencode\skills") -Recurse -Filter SKILL.md -File -ErrorAction SilentlyContinue).Count
$memoryCount = @(Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot "memory") -Recurse -Filter *.md -File -ErrorAction SilentlyContinue).Count

if ($missing.Count -gt 0) {
    Write-Output "INTEGRITY-FAIL"
    $missing | ForEach-Object { Write-Output "Missing: $_" }
    exit 1
}

Write-Output "INTEGRITY-PASS"
Write-Output "Skill files: $skillCount"
Write-Output "Memory Markdown files: $memoryCount"
Write-Output "All required portable layers are present."
