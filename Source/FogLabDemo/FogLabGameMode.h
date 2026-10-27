#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "FogLabGameMode.generated.h"

/** Asset-free diagnostic scene. Units are UE centimetres. */
UCLASS()
class FOGLABDEMO_API AFogLabGameMode : public AGameModeBase
{
    GENERATED_BODY()
public:
    AFogLabGameMode();
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
private:
    TSharedPtr<class FFogLabComparison> Comparison;
    void OnCaptureProcessed();
    FString CapturePath;
    int32 RenderedTicks = 0;
    int32 CaptureFrame = 120;
    bool bCaptureRequested = false;
    FDelegateHandle CaptureHandle;
    FString ReadbackDirectory;
    double ReadbackStarted = 0;
    float ReadbackTimeout = 120;
};
