#pragma once

#include "CoreMinimal.h"

/** One render-thread snapshot drives both the shader and diagnostic metadata. */
struct FUEFogLighting
{
    int32 Model = 0;
    float Albedo = .8f;
    float G = .5f;
    FVector3f IncidentWeight = FVector3f(4, 3, 2);
    FVector3f PhotonDirection = FVector3f(-1, -1, .15f);
};

bool ApplyUEFogLightingLaunch(FString& Error);
bool GetUEFogLightingSnapshot(FUEFogLighting& Out, FString& Error);
