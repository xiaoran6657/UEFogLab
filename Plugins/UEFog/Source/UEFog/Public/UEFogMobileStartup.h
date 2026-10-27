#pragma once

#include "CoreMinimal.h"

class UWorld;
class UStaticMesh;
class AActor;
class ISceneViewExtension;

/** Default-off PRE-02 startup observation; adds no rendering/GPU resource pass. */
UEFOG_API bool ApplyUEFogMobileStartupLaunch(FString& Error);
UEFOG_API TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> CreateUEFogMobileStartupExtension();
UEFOG_API void UEFogMobileStartupSceneReady(UWorld* World, UStaticMesh* Cube, AActor* Camera);
UEFOG_API void TickUEFogMobileStartup();
UEFOG_API void ShutdownUEFogMobileStartup();
