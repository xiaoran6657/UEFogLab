#pragma once
#include "CoreMinimal.h"
class ISceneViewExtension;
class UWorld;
class FJsonObject;

UEFOG_API bool ApplyUEFogMobileReconstructionLaunch(FString& Error);
UEFOG_API TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> CreateUEFogMobileReconstructionExtension();
UEFOG_API void UEFogMobileReconstructionFixtureReady(UWorld* World, TSharedRef<FJsonObject> Fixture);
UEFOG_API void ShutdownUEFogMobileReconstruction();
