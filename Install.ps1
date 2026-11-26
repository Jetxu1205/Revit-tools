$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$configPath = Join-Path $env:APPDATA 'pyRevit/pyRevit_config.ini'
if (-not (Test-Path -LiteralPath $configPath)) { throw 'pyRevit configuration was not found.' }
$configText = [IO.File]::ReadAllText($configPath)
$match = [regex]::Match($configText, '(?m)^userextensions\s*=\s*(\[.*\])\s*$')
if (-not $match.Success) { throw 'Cannot find userextensions in the pyRevit configuration.' }
$roots = @($match.Groups[1].Value | ConvertFrom-Json)
if ($roots -notcontains $repoRoot) {
    Copy-Item -LiteralPath $configPath -Destination ($configPath + '.JetTools.' + (Get-Date -Format 'yyyyMMddHHmmss') + '.bak')
    $roots += $repoRoot
    $replacement = 'userextensions = ' + (ConvertTo-Json -InputObject @($roots) -Compress)
    $configText = $configText.Substring(0,$match.Index) + $replacement + "`r`n" + $configText.Substring($match.Index+$match.Length)
    [IO.File]::WriteAllText($configPath,$configText,(New-Object Text.UTF8Encoding($false)))
}
Write-Output 'Registered Jet Tools. Click pyRevit > Reload in Revit.'
