#pragma once
#include "CoreMinimal.h"
class ISceneViewExtension;
class UWorld;
class FJsonObject;

/** Isolated M4 Uniform/Prescribed Full64. No argument means no extension. */
UEFOG_API bool ApplyUEFogMobileFogLaunch(FString& Error);
UEFOG_API TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> CreateUEFogMobileFogExtension();
UEFOG_API void UEFogMobileFogFixtureReady(UWorld* World, TSharedRef<FJsonObject> Fixture);
