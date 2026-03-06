[CmdletBinding()]
param(
    [string]$Configuration = 'Release',
    [string]$RuntimeIdentifier = 'win-x64',
    [switch]$SkipExeRun,
    [switch]$NoClean
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
$staticProject = ".\\src\\package-static-apphost\\StaticAppHost.csproj"
$consumerProject = ".\\samples\\simple-pinvoke\\SimplePInvoke.csproj"
$consumerDir = ".\\samples\\simple-pinvoke"
$packageAssetsDir = (Join-Path $repoRoot "artifacts\\hostlibs\\$RuntimeIdentifier")

Push-Location $repoRoot

try {
    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $logDir = Join-Path $repoRoot "artifacts\\logs\\matrix\\run-$timestamp"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null

    if (-not $NoClean) {
        Write-Host '==> [00-clean] remove simple-pinvoke/bin and simple-pinvoke/obj'
        foreach ($p in @("$consumerDir\\bin", "$consumerDir\\obj")) {
            if (Test-Path $p) {
                Remove-Item -Recurse -Force $p
            }
        }
    }

    $netTarget = (Select-String -Path $consumerProject -Pattern '<TargetFramework>([^<]+)</TargetFramework>' | Select-Object -First 1).Matches.Groups[1].Value
    if (-not $netTarget) {
        throw 'Unable to resolve TargetFramework from SimplePInvoke.csproj'
    }

    $steps = @(
        [pscustomobject]@{
            Name = '01-pack';
            Command = "dotnet pack $staticProject -c $Configuration /p:PackageAssetsDir=$packageAssetsDir -v:minimal";
            ExpectIncrementalLinker = $null
        },
        [pscustomobject]@{
            Name = '02-build-no-r-first';
            Command = "dotnet build $consumerProject -c $Configuration -v:minimal";
            ExpectIncrementalLinker = $true
        },
        [pscustomobject]@{
            Name = '03-build-no-r-second';
            Command = "dotnet build $consumerProject -c $Configuration -v:minimal";
            ExpectIncrementalLinker = $false
        },
        [pscustomobject]@{
            Name = '04-build-r-first';
            Command = "dotnet build $consumerProject -c $Configuration -r $RuntimeIdentifier -v:minimal";
            ExpectIncrementalLinker = $true
        },
        [pscustomobject]@{
            Name = '05-build-r-second';
            Command = "dotnet build $consumerProject -c $Configuration -r $RuntimeIdentifier -v:minimal";
            ExpectIncrementalLinker = $false
        },
        [pscustomobject]@{
            Name = '06-publish-first';
            Command = "dotnet publish $consumerProject -c $Configuration -r $RuntimeIdentifier /p:PublishSingleFile=true -v:minimal";
            ExpectIncrementalLinker = $true
        },
        [pscustomobject]@{
            Name = '07-publish-second';
            Command = "dotnet publish $consumerProject -c $Configuration -r $RuntimeIdentifier /p:PublishSingleFile=true -v:minimal";
            ExpectIncrementalLinker = $false
        }
    )

    $results = [System.Collections.Generic.List[object]]::new()

    foreach ($step in $steps) {
        $logFile = Join-Path $logDir ("$($step.Name).log")
        Write-Host "==> [$($step.Name)] $($step.Command)"

        $output = Invoke-Expression "$($step.Command) 2>&1"
        $output | Tee-Object -FilePath $logFile | Out-Host

        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "Step $($step.Name) failed with exit code $exitCode. See: $logFile"
        }

        $hasIncrementalLinker = [bool]($output | Select-String -Pattern 'Incremental Linker' -SimpleMatch -Quiet)

        $validation = 'N/A'
        $validationPass = $true
        if ($null -ne $step.ExpectIncrementalLinker) {
            $validation = if ($step.ExpectIncrementalLinker) { 'Expect HIT' } else { 'Expect MISS' }
            $validationPass = ($hasIncrementalLinker -eq [bool]$step.ExpectIncrementalLinker)
            if (-not $validationPass) {
                $actual = if ($hasIncrementalLinker) { 'HIT' } else { 'MISS' }
                throw "Step $($step.Name) linker validation failed. Expected $validation, actual: $actual. See: $logFile"
            }
        }

        $results.Add([pscustomobject]@{
            Step = $step.Name
            Log = $logFile
            ExitCode = $exitCode
            IncrementalLinker = if ($hasIncrementalLinker) { 'HIT' } else { 'MISS' }
            Validation = $validation
            Passed = $validationPass
        }) | Out-Null
    }

    if (-not $SkipExeRun) {
        $exePath = Join-Path $repoRoot "samples\\simple-pinvoke\\bin\\$Configuration\\$netTarget\\$RuntimeIdentifier\\publish\\SimplePInvoke.exe"
        if (-not (Test-Path $exePath)) {
            throw "Executable not found: $exePath"
        }

        $runLog = Join-Path $logDir '08-run-exe.log'
        Write-Host "==> [08-run-exe] $exePath"

        & $exePath 2>&1 | Tee-Object -FilePath $runLog | Out-Host
        $exeExitCode = $LASTEXITCODE
        if ($exeExitCode -ne 0) {
            throw "Step 08-run-exe failed with exit code $exeExitCode. See: $runLog"
        }

        $results.Add([pscustomobject]@{
            Step = '08-run-exe'
            Log = $runLog
            ExitCode = $exeExitCode
            IncrementalLinker = 'N/A'
            Validation = 'Exe exit code == 0'
            Passed = $true
        }) | Out-Null
    }

    Write-Host ''
    Write-Host '==== Summary ===='
    $results | Format-Table -AutoSize | Out-String | Write-Host
    Write-Host "Logs: $logDir"
}
catch {
    Write-Error $_
    exit 1
}
finally {
    Pop-Location
}
