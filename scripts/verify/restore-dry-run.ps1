[CmdletBinding()]
param(
    [Parameter()]
    [string]$RepositoryRoot
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("axel-portable-restore-" + [Guid]::NewGuid().ToString("N"))
$bootstrap = Join-Path $RepositoryRoot "scripts\bootstrap\bootstrap.ps1"

try {
    & $bootstrap -ProfileRoot $tempRoot
    $expected = @(
        "AGENTS.md",
        "identity\AXEL.md",
        "memory\basic-memory\axel",
        "memory\basic-memory\main",
        "memory\basic-memory\axel-project-knowledge",
        "opencode\opencode.json",
        "memory\config.json",
        "runtimes\codex\config.toml.template",
        "runtimes\paseo\config.template.json"
    )
    $missing = @($expected | Where-Object { -not (Test-Path -LiteralPath (Join-Path $tempRoot $_)) })
    if ($missing.Count -gt 0) {
        Write-Output "RESTORE-DRY-RUN-FAIL"
        $missing | ForEach-Object { Write-Output "Missing restored path: $_" }
        exit 1
    }

    $config = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $tempRoot "opencode\opencode.json") | ConvertFrom-Json
    if ($null -eq $config.mcp."basic-memory" -or $null -eq $config.mcp.codemem) {
        throw "Generated OpenCode config is missing required MCP definitions."
    }

    Write-Output "RESTORE-DRY-RUN-PASS"
    Write-Output "Temporary profile contained identity, memory, skills, runtime templates and MCP configuration."
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
