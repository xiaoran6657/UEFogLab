#pragma once
#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class FSceneViewFamily;

// Opt-in demo values only. Actor access stays on the game thread. Each immutable
// snapshot is bound at BeginRenderViewFamily, after UE assigns its scene frame.
struct FUEFogDynamicState
{
    int32 OutputIndex = -1;
    double TimeSeconds = 0;
    double Phase = 0;
    uint64 GameFrame = 0;
    FVector SphereWorldCm = FVector(300, 0, 180);
    FVector PhotonDirection = FVector(-1, -1, .15).GetSafeNormal();
    // FString owns its value. No Json TSharedPtr reference counters cross GT/RT.
    FString ActorJson;
};

UEFOG_API bool IsUEFogDynamicActive();
UEFOG_API void PublishUEFogDynamicState(const FUEFogDynamicState& State);
UEFOG_API void BindUEFogDynamicView(const FSceneViewFamily& Family);
UEFOG_API bool GetUEFogDynamicState(uint32 Frame, FUEFogDynamicState& State);
