#pragma once
#include "CoreMinimal.h"
class ISceneViewExtension;
class UWorld;
class FJsonObject;

/** PRE-03 only; ordinary/default runs create no probe extension or GPU work. */
UEFOG_API bool ApplyUEFogMobileProbeLaunch(FString& Error);
UEFOG_API TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> CreateUEFogMobileProbeExtension();
UEFOG_API void UEFogMobileProbeFixtureReady(UWorld* World, TSharedRef<FJsonObject> Fixture);
UEFOG_API void UEFogMobileProbePrepareCookContent();
