param(
    [Parameter(Mandatory)][string]$Output,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
& $Python "$PSScriptRoot/mobile_reconstruction_reference.py" cpu --output $Output
if ($LASTEXITCODE -ne 0) { throw "Mobile reconstruction CPU reference rejected (exit $LASTEXITCODE)." }
