[CmdletBinding()]
param(
    [Parameter()]
    [string]$RepositoryRoot
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

$excludedDirectoryNames = @(".git", "node_modules", "dist", "build", "__pycache__", ".venv")
$excludedFiles = @("secret-scan.ps1")
$textExtensions = @(".md", ".json", ".jsonc", ".toml", ".yaml", ".yml", ".txt", ".ps1", ".psm1", ".js", ".mjs", ".ts", ".tsx", ".py", ".sh", ".lock", ".example", ".conf")
$rules = @(
    @{ Name = "private-key"; Pattern = "-----BEGIN [^-]+ PRIVATE KEY-----" },
    @{ Name = "github-token"; Pattern = "(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9_]{20,}" },
    @{ Name = "github-pat"; Pattern = "(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,}" },
    @{ Name = "openai-key"; Pattern = "(?<![A-Za-z0-9_])sk-[A-Za-z0-9]{20,}" },
    @{ Name = "composio-key"; Pattern = "(?<![A-Za-z0-9_])ck_[A-Za-z0-9_-]{16,}" },
    @{ Name = "google-token"; Pattern = "(?<![A-Za-z0-9_])AQ\.[A-Za-z0-9_-]{20,}" },
    @{ Name = "bearer-token"; Pattern = "(?i)(?<![A-Za-z0-9_])Bearer\s+[A-Za-z0-9._~-]{24,}" },
    @{ Name = "aws-access-key"; Pattern = "(?<![A-Za-z0-9_])AKIA[A-Z0-9]{16}" },
    @{ Name = "oauth-client-secret"; Pattern = "(?i)client_secret\s*[:=]\s*[A-Za-z0-9._-]{20,}" }
)

$hits = @()
$files = Get-ChildItem -LiteralPath $RepositoryRoot -Recurse -Force -File | Where-Object {
    $relative = $_.FullName.Substring($RepositoryRoot.Length).TrimStart("\", "/")
    $parts = $relative -split "[\\/]"
    ($excludedFiles -notcontains $_.Name) -and
    (($parts | Where-Object { $excludedDirectoryNames -contains $_ }).Count -eq 0) -and
    (($textExtensions -contains $_.Extension.ToLowerInvariant()) -or $_.Name -match "\.env\.example$")
}

foreach ($file in $files) {
    try {
        $lines = [IO.File]::ReadAllLines($file.FullName)
    } catch {
        continue
    }

    for ($lineNumber = 0; $lineNumber -lt $lines.Count; $lineNumber++) {
    $line = $lines[$lineNumber]
        $relativeFile = $file.FullName.Substring($RepositoryRoot.Length).TrimStart("\", "/")
        $context = $line.ToLowerInvariant()
        $isTestOrFixtureFile = $relativeFile -match "(?i)(\\test\\|\.test\.|fixtures|secret-scanner|bird-search)"
        $looksLikeTestOrExample = $context -match "example|fixture|test|regex|pattern|placeholder|dummy|fake|sample|redact|assert|expect|secret.?scanner"
        $looksLikeCodeTemplate = $context -match "\$\{|process\.env|new regexp"
        $looksLikeAssignment = $line -match "[:=]"

        foreach ($rule in $rules) {
            if (-not [regex]::IsMatch($line, $rule.Pattern)) {
                continue
            }

            if ($isTestOrFixtureFile -or $looksLikeTestOrExample) {
                continue
            }

            if ($looksLikeCodeTemplate -and (@("bearer-token", "oauth-client-secret") -contains $rule.Name)) {
                continue
            }

            if ((@("github-token", "github-pat", "openai-key", "composio-key", "google-token", "aws-access-key") -contains $rule.Name) -and (-not $looksLikeAssignment)) {
                continue
            }

            $hits += [PSCustomObject]@{
                File = $file.FullName.Substring($RepositoryRoot.Length).TrimStart("\", "/")
                Rule = $rule.Name
                Line = $lineNumber + 1
            }
        }
    }
}

if ($hits.Count -gt 0) {
    Write-Output "SECRET-SCAN-FAIL"
    $hits | Sort-Object File, Rule, Line -Unique | ForEach-Object {
        Write-Output ("Potential {0} match in {1}:{2}" -f $_.Rule, $_.File, $_.Line)
    }
    exit 1
}

Write-Output "SECRET-SCAN-PASS: no high-confidence credential patterns found"
