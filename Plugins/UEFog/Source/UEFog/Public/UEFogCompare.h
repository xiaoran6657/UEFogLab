#pragma once
#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class FSceneView;
class FRDGBuilder;

// Opt-in observation only; the numeric readback contract is deliberately separate.
UEFOG_API bool ApplyUEFogCompareLaunch(FString& Error);
UEFOG_API bool IsUEFogCompareActive();
UEFOG_API TSharedPtr<FJsonObject> GetUEFogCompareRequest();
UEFOG_API bool ApplyUEFogCompareCVars(FString& Error);
UEFOG_API void ObserveUEFogCompareView(FRDGBuilder& GraphBuilder, const FSceneView& View,
    const FIntRect& Rect, FIntPoint Extent);
UEFOG_API void CountUEFogComparePass(uint32 Frame, bool bComposite);
UEFOG_API TSharedPtr<FJsonObject> GetUEFogCompareView();
UEFOG_API void ArmUEFogCompareScreenshot();
UEFOG_API TSharedPtr<FJsonObject> GetUEFogCompareScreenshotView();
UEFOG_API void ObserveUEFogDynamicShaderInputs(uint32 Frame, const FVector& SphereTranslated,
    const FVector& PreViewTranslation, const FVector& Photon, FIntPoint FogSize, uint32 Steps);
