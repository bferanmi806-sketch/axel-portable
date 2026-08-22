[CmdletBinding()]
param(
    [Parameter()]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$checks = @(
    @{ Name = "Canonical operating rules"; RelativePath = "AGENTS.md" },
    @{ Name = "Canonical Axel prompt"; RelativePath = "identity\AXEL.md" },
    @{ Name = "Basic Memory Axel project"; RelativePath = "memory\basic-memory\axel" },
    @{ Name = "OpenCode template"; RelativePath = "opencode\config\opencode.json.template" },
    @{ Name = "CodeMem source snapshot"; RelativePath = "tools\integrations\codemem" },
    @{ Name = "Improvement Engine"; RelativePath = "tools\integrations\axel-improvement-engine" },
    @{ Name = "Codex template"; RelativePath = "runtimes\codex\config.toml.template" },
    @{ Name = "Paseo template"; RelativePath = "runtimes\paseo\config.template.json" }
)

$lines = @(
    "# Axel Portable Inventory Check",
    "",
    "Generated: $([DateTime]::UtcNow.ToString('o'))",
    "",
    "| Component | Path | Status |",
    "|---|---|---|"
)

foreach ($check in $checks) {
    $path = Join-Path $repoRoot $check.RelativePath
    $exists = Test-Path -LiteralPath $path
    $status = if ($exists) { "present" } else { "MISSING" }
    $lines += "| $($check.Name) | ``$($check.RelativePath)`` | $status |"
}

$lines += "Source-path details and exclusions are in ``manifests/FILES.md``."

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $lines | ForEach-Object { Write-Output $_ }
} else {
    Set-Content -LiteralPath $OutputPath -Value $lines -Encoding UTF8
    Write-Output "Inventory written: $OutputPath"
}
