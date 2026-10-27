#pragma once
#include "CoreMinimal.h"
class FRDGBuilder;
class FSceneView;
class FJsonObject;

UEFOG_API bool ApplyUEFogMobilePerformanceLaunch(FString& Error);
UEFOG_API bool IsUEFogMobilePerformanceActive();
UEFOG_API void UEFogMobilePerformanceBegin(FRDGBuilder& Graph, const FSceneView& View);
UEFOG_API void UEFogMobilePerformanceBoundary(FRDGBuilder& Graph, uint32 Boundary);
UEFOG_API bool UEFogMobilePerformanceNeedsSnapshot();
UEFOG_API void UEFogMobilePerformanceWitness(TSharedPtr<FJsonObject> Snapshot, bool SameResource, uint32 Integrate, uint32 Reconstruct, uint32 Composite);
UEFOG_API void UEFogMobilePerformanceEnd(FRDGBuilder& Graph, const FSceneView& View);
UEFOG_API void ShutdownUEFogMobilePerformance();
