# Shared, strict request validation. No UE process is started by this helper.
function ConvertTo-FogVector([string]$Text) {
    $Parts = $Text.Split(',')
    if ($Parts.Count -ne 3) { throw 'Lighting vector requires three CSV numbers.' }
    $Result = @($Parts | ForEach-Object {
        $Number = 0.0
        if (![double]::TryParse($_,[Globalization.NumberStyles]::Float,[cultureinfo]::InvariantCulture,[ref]$Number) -or ![double]::IsFinite($Number)) { throw 'Lighting vector must be finite invariant-culture numbers.' }
        $Number
    })
    return ,$Result
}

function Get-FogLighting([string]$Model,[double]$Albedo,[double]$G,[string]$Weight,[string]$Direction,$Bound) {
    if ($Model -eq 'Prescribed') {
        foreach ($Key in @('Albedo','HGg','IncidentWeight','PhotonDirection')) {
            if ($Bound.ContainsKey($Key)) { throw 'Explicit lighting parameters require -LightingModel ManualDirectionalHG.' }
        }
        return @{}
    }
    if (![double]::IsFinite($Albedo) -or $Albedo -lt 0 -or $Albedo -gt 1) { throw 'Albedo outside [0,1].' }
    if (![double]::IsFinite($G) -or [Math]::Abs($G) -gt 0.8) { throw 'HGg outside [-0.8,0.8].' }
    $E = ConvertTo-FogVector $Weight
    if (@($E | Where-Object { $_ -lt 0 -or $_ -gt 16 }).Count) { throw 'Incident weight outside [0,16].' }
    $K = ConvertTo-FogVector $Direction
    $Scale = ($K | ForEach-Object { [Math]::Abs($_) } | Measure-Object -Maximum).Maximum
    if ($Scale -eq 0) { throw 'Photon direction must be nonzero.' }
    $Unit = @($K | ForEach-Object { $_ / $Scale })
    $Length = [Math]::Sqrt(($Unit | ForEach-Object { $_ * $_ } | Measure-Object -Sum).Sum)
    $Unit = @($Unit | ForEach-Object { $_ / $Length })
    return @{schemaVersion=2;modelKey='ManualDirectionalHG-v1';lightingInput='manual';directionConvention='photon-propagation-to-camera-v1';albedo=$Albedo;g=$G;incidentWeightRGB=$E;photonDirectionRequestedWorld=$K;photonDirectionWorld=$Unit;lightTransmittance=1;lightVisibility=1}
}

function Get-FogLightingArguments($Lighting) {
    if (!$Lighting.Count) { return @('-FogLabLightingModel=0') }
    $Format = { param($Value) $Value.ToString('R',[cultureinfo]::InvariantCulture) }
    $E = ($Lighting.incidentWeightRGB | ForEach-Object { & $Format $_ }) -join ','
    $K = ($Lighting.photonDirectionWorld | ForEach-Object { & $Format $_ }) -join ','
    return @('-FogLabLightingModel=1',"-FogLabAlbedo=$(& $Format $Lighting.albedo)","-FogLabHGg=$(& $Format $Lighting.g)","-FogLabIncidentWeight=$E","-FogLabPhotonDirection=$K")
}

function Assert-FogLightingSnapshot($Lighting,$Meta) {
    if (!$Lighting.Count) {
        if ($Meta.schemaVersion -ne 1 -or $Meta.PSObject.Properties.Name -contains 'modelKey') { throw 'Expected Prescribed/schema1.' }
        return
    }
    foreach ($Key in @('schemaVersion','modelKey','lightingInput','directionConvention','lightTransmittance','lightVisibility')) {
        if ($null -eq $Meta.$Key -or $Meta.$Key -ne $Lighting[$Key]) { throw "Effective lighting mismatch: $Key" }
    }
    if ($Meta.PSObject.Properties.Name -contains 'sourceFunction') { throw 'Manual HG must not declare a fixed sourceFunction.' }
    foreach ($Key in @('albedo','g','incidentWeightRGB','photonDirectionWorld')) {
        $Expected = @($Lighting[$Key]); $Actual = @($Meta.$Key)
        if ($Actual.Count -ne $Expected.Count) { throw "Missing/malformed effective $Key" }
        for ($Index=0; $Index -lt $Expected.Count; $Index++) {
            if (![double]::IsFinite($Actual[$Index]) -or [Math]::Abs($Actual[$Index]-$Expected[$Index]) -gt (2e-7+2e-6*[Math]::Abs($Expected[$Index]))) { throw "Effective lighting mismatch: $Key" }
        }
    }
    $Norm = [Math]::Sqrt(($Meta.photonDirectionWorld | ForEach-Object { $_ * $_ } | Measure-Object -Sum).Sum)
    if ([Math]::Abs($Norm-1) -gt 2e-6) { throw 'Effective photon direction is not normalized.' }
}
