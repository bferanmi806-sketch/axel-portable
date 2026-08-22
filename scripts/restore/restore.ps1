[CmdletBinding()]
param(
    [Parameter()]
    [string]$ProfileRoot,

    [Parameter()]
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$bootstrap = Join-Path $PSScriptRoot "..\bootstrap\bootstrap.ps1"
& $bootstrap -ProfileRoot $ProfileRoot -Force:$Force

Write-Output ""
Write-Output "Restore preparation completed without changing the live installation."
Write-Output "Review RESTORE.md for Basic Memory registration and runtime-specific activation steps."
