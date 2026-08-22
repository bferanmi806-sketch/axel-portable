[CmdletBinding()]
param(
    [Parameter()]
    [string]$ProfileRoot,

    [Parameter()]
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if ([string]::IsNullOrWhiteSpace($ProfileRoot)) {
    $ProfileRoot = Join-Path $HOME "Axel-Portable-Profile"
}

$requiredFiles = @(
    "AGENTS.md",
    "identity\AXEL.md",
    "opencode\config\opencode.json.template",
    "memory\config.template.json"
)

foreach ($relativePath in $requiredFiles) {
    $sourcePath = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Portable package is incomplete: $relativePath"
    }
}

if ((Test-Path -LiteralPath $ProfileRoot) -and (-not $Force)) {
    $existing = Get-ChildItem -LiteralPath $ProfileRoot -Force -ErrorAction SilentlyContinue
    if ($existing) {
        throw "Profile already exists and is not empty: $ProfileRoot. Use -Force only for a disposable profile."
    }
}

New-Item -ItemType Directory -Path $ProfileRoot -Force | Out-Null

function Copy-PortableTree {
    param(
        [string]$RelativeSource,
        [string]$RelativeDestination
    )

    $source = Join-Path $repoRoot $RelativeSource
    $destination = Join-Path $ProfileRoot $RelativeDestination
    if (Test-Path -LiteralPath $source -PathType Container) {
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        Copy-Item -Path (Join-Path $source "*") -Destination $destination -Recurse -Force
    }
}

Copy-Item -LiteralPath (Join-Path $repoRoot "AGENTS.md") -Destination (Join-Path $ProfileRoot "AGENTS.md") -Force
Copy-PortableTree "identity" "identity"
Copy-PortableTree "memory\basic-memory" "memory\basic-memory"
Copy-PortableTree "memory\axel-project" "memory\axel-project"
Copy-PortableTree "opencode\skills" "opencode\skills"
Copy-PortableTree "opencode\commands" "opencode\commands"
Copy-PortableTree "opencode\plans" "opencode\plans"
Copy-PortableTree "tools\mcp" "tools\mcp"
Copy-PortableTree "runtimes" "runtimes"

$opencodeTemplate = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $repoRoot "opencode\config\opencode.json.template")
$opencodeRoot = $repoRoot.Replace("\", "/")
$opencodeConfig = $opencodeTemplate.Replace("__AXEL_PORTABLE_ROOT__", $opencodeRoot)
$opencodeDestination = Join-Path $ProfileRoot "opencode\opencode.json"
New-Item -ItemType Directory -Path (Split-Path -Parent $opencodeDestination) -Force | Out-Null
Set-Content -LiteralPath $opencodeDestination -Value $opencodeConfig -Encoding UTF8 -NoNewline
$null = $opencodeConfig | ConvertFrom-Json

$memoryTemplate = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $repoRoot "memory\config.template.json")
$memoryConfig = $memoryTemplate.Replace("__AXEL_PORTABLE_ROOT__", $opencodeRoot)
$memoryDestination = Join-Path $ProfileRoot "memory\config.json"
Set-Content -LiteralPath $memoryDestination -Value $memoryConfig -Encoding UTF8 -NoNewline
$null = $memoryConfig | ConvertFrom-Json

$notes = @"
# Axel Portable Profile

This profile was generated from:

$repoRoot

It contains copied identity, human-readable memory sources, skills, MCP
references and sanitized runtime templates. It does not contain credentials.

The generated OpenCode config points back to the vendored CodeMem source in the
portable repository. Build that source before launching OpenCode with the
config. Register Basic Memory projects explicitly and review all provider
credentials before enabling remote integrations.

The current installation was not overwritten by this bootstrap operation.
"@
Set-Content -LiteralPath (Join-Path $ProfileRoot "RESTORE-NOTES.md") -Value $notes -Encoding UTF8

Write-Output "Profile created: $ProfileRoot"
Write-Output "OpenCode template materialized: $opencodeDestination"
Write-Output "Basic Memory template materialized: $memoryDestination"
Write-Output "Next: build tools/integrations/codemem, register Basic Memory projects, then run verification in a disposable runtime."
