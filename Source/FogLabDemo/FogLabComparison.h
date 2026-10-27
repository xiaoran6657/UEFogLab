#pragma once
#include "CoreMinimal.h"

class UWorld;
class FJsonObject;
class ACameraActor;
class ALocalFogVolume;
class AExponentialHeightFog;
class ADirectionalLight;
class APointLight;

class FFogLabComparison : public TSharedFromThis<FFogLabComparison>
{
public:
    void Start(UWorld* World);
    void Tick();
private:
    void Capture(int32 Width, int32 Height, const TArray<FColor>& Pixels);
    TSharedPtr<FJsonObject> ActorSnapshot() const;
    TWeakObjectPtr<UWorld> Scene;
    TWeakObjectPtr<ACameraActor> Camera;
    TWeakObjectPtr<ALocalFogVolume> Local;
    TWeakObjectPtr<AExponentialHeightFog> Container;
    TWeakObjectPtr<ADirectionalLight> Sun;
    TWeakObjectPtr<APointLight> Fill;
    TArray<TWeakObjectPtr<AActor>> Geometry;
    bool bRequested = false;
    bool bCaptured = false;
    uint64 CapturedView = 0;
    int32 ProfilesRequested = 0;
    double Started = 0;
    FDelegateHandle Handle;
};
