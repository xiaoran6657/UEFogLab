param(
    [ValidateSet('Run', 'Validate', 'Negative')][string]$Action = 'Run',
    [Parameter(Mandatory)][string]$Output,
    [string]$Candidate = '',
    [string]$PreviousCandidate = '',
    [string]$InputRun = '',
    [string]$Serial = '',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$RunArgs = @($Action.ToLowerInvariant(), '--output', $Output)
if ($Action -eq 'Run') {
    $RunArgs += @('--candidate', $Candidate)
    if ($PreviousCandidate) { $RunArgs += @('--previous-candidate', $PreviousCandidate) }
    if ($Serial) { $RunArgs += @('--serial', $Serial) }
} else { $RunArgs += @('--input', $InputRun) }
& $Python "$PSScriptRoot/android_run.py" @RunArgs
if ($LASTEXITCODE -ne 0) { throw "Android startup $Action rejected (exit $LASTEXITCODE)." }
