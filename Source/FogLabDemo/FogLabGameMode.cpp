#include "FogLabGameMode.h"
#include "FogLabComparison.h"
#include "UEFogCompare.h"
#include "UEFogMobileStartup.h"
#include "FogLabMobileFixture.h"
#include "FogLabMobileFogFixture.h"
#include "FogLabMobileReconstructionFixture.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/PointLight.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "HighResScreenshot.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "UnrealClient.h"
#include "HAL/FileManager.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"

AFogLabGameMode::AFogLabGameMode()
{
    PrimaryActorTick.bCanEverTick = true;
    DefaultPawnClass = nullptr;
}

void AFogLabGameMode::BeginPlay()
{
    Super::BeginPlay();
    UWorld* World = GetWorld();
    if (StartFogLabMobileReconstructionFixture(World)) return;
    if (IsUEFogCompareActive())
    {
        Comparison = MakeShared<FFogLabComparison>();
        Comparison->Start(World);
        return;
    }
    if (StartFogLabMobileFogFixture(World)) return;
    if (StartFogLabMobileFixture(World)) return;
    UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
    check(Cube);
    FString Fixture = TEXT("Diagnostic"), CameraMode = TEXT("Outside");
    float OriginOffset = 0;
    FParse::Value(FCommandLine::Get(), TEXT("FogLabFixture="), Fixture);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabCamera="), CameraMode);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabOriginOffset="), OriginOffset);
    const FVector WorldOffset(OriginOffset, 0, 0);
    auto AddCube = [World, Cube, WorldOffset](const FVector& Position, const FVector& Scale)
    {
        AStaticMeshActor* Actor = World->SpawnActor<AStaticMeshActor>(Position + WorldOffset, FRotator::ZeroRotator);
        Actor->GetStaticMeshComponent()->SetMobility(EComponentMobility::Movable);
        Actor->GetStaticMeshComponent()->SetStaticMesh(Cube);
        Actor->SetActorScale3D(Scale);
    };
    if (Fixture == TEXT("Diagnostic"))
    {
    AddCube(FVector(200, 0, -35), FVector(24, 20, 0.5));
    AddCube(FVector(900, 0, 280), FVector(0.3, 18, 6));
    for (int32 Index = -3; Index <= 3; ++Index)
    {
        AddCube(FVector(250, Index * 190, 150), FVector(0.6, 0.6, 3));
    }
    for (int32 Index = -2; Index <= 2; ++Index)
    {
        AddCube(FVector(-150, Index * 85, 125), FVector(0.06, 0.06, 2.5));
    }
    }
    if (Fixture == TEXT("Occluded")) AddCube(FVector(-250, 0, 180), FVector(0.2,30,30));
    ADirectionalLight* Sun = World->SpawnActor<ADirectionalLight>(FVector(0, 0, 600) + WorldOffset, FRotator(-45, -30, 0));
    Sun->GetLightComponent()->SetMobility(EComponentMobility::Movable);
    Sun->GetLightComponent()->SetIntensity(3.0f);
    APointLight* Fill = World->SpawnActor<APointLight>(FVector(-450, -350, 400) + WorldOffset, FRotator::ZeroRotator);
    Fill->GetLightComponent()->SetMobility(EComponentMobility::Movable);
    Fill->GetLightComponent()->SetIntensity(25000.0f);
    CastChecked<UPointLightComponent>(Fill->GetLightComponent())->SetAttenuationRadius(2200.0f);

    APostProcessVolume* Exposure = World->SpawnActor<APostProcessVolume>();
    Exposure->bUnbound = true;
    Exposure->Settings.bOverride_AutoExposureMethod = true;
    Exposure->Settings.AutoExposureMethod = EAutoExposureMethod::AEM_Manual;
    Exposure->Settings.bOverride_AutoExposureApplyPhysicalCameraExposure = true;
    Exposure->Settings.AutoExposureApplyPhysicalCameraExposure = false;

    FVector Eye(-850, -950, 310), Target(280, 0, 145);
    if (CameraMode == TEXT("Inside")) { Eye = FVector(300,0,180); Target = FVector(800,100,190); }
    if (CameraMode == TEXT("Oblique")) { Eye = FVector(-350,-950,600); Target = FVector(300,0,180); }
    if (Fixture == TEXT("Occluded")) { Eye = FVector(-850,0,180); Target = FVector(300,0,180); }
    ACameraActor* Camera = World->SpawnActor<ACameraActor>(Eye + WorldOffset, (Target - Eye).Rotation());
    Camera->GetCameraComponent()->SetFieldOfView(60.0f);
    Camera->GetCameraComponent()->bConstrainAspectRatio = false;
    float Aspect = 0;
    if (FParse::Value(FCommandLine::Get(), TEXT("FogLabConstrainedAspect="), Aspect) && Aspect > 0)
    {
        Camera->GetCameraComponent()->bConstrainAspectRatio = true;
        Camera->GetCameraComponent()->SetAspectRatio(Aspect);
    }
    if (APlayerController* Controller = World->GetFirstPlayerController())
    {
        Controller->SetViewTarget(Camera);
    }
    FParse::Value(FCommandLine::Get(), TEXT("FogLabCapture="), CapturePath);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabFrames="), CaptureFrame);
    CaptureFrame = FMath::Max(CaptureFrame, 10);
    if (!CapturePath.IsEmpty())
    {
        CaptureHandle = FScreenshotRequest::OnScreenshotRequestProcessed().AddUObject(this, &AFogLabGameMode::OnCaptureProcessed);
    }
    FParse::Value(FCommandLine::Get(), TEXT("FogLabReadback="), ReadbackDirectory);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabReadbackTimeout="), ReadbackTimeout);
    ReadbackStarted = FPlatformTime::Seconds();
    UE_LOG(LogTemp, Display, TEXT("UEFogLab diagnostic scene ready. Native fog disabled; explicit custom fog model (Prescribed or manual directional HG)."));
    UEFogMobileStartupSceneReady(World, Cube, Camera);
}

void AFogLabGameMode::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (Comparison) { Comparison->Tick(); return; }
    TickUEFogMobileStartup();
    ++RenderedTicks;
    if (!ReadbackDirectory.IsEmpty())
    {
        if (IFileManager::Get().FileExists(*FPaths::Combine(ReadbackDirectory, TEXT("failed.txt"))))
            FPlatformMisc::RequestExitWithStatus(false, 3);
        else if (IFileManager::Get().FileExists(*FPaths::Combine(ReadbackDirectory, TEXT("metadata.json"))))
            FPlatformMisc::RequestExitWithStatus(false, 0);
        else if (FPlatformTime::Seconds() - ReadbackStarted > ReadbackTimeout)
        {
            IFileManager::Get().MakeDirectory(*ReadbackDirectory, true);
            FFileHelper::SaveStringToFile(TEXT("Numeric readback timed out before completion"), *FPaths::Combine(ReadbackDirectory, TEXT("failed.txt")));
            UE_LOG(LogTemp, Error, TEXT("UEFog numeric readback timed out."));
            FPlatformMisc::RequestExitWithStatus(false, 4);
        }
    }
    if (!CapturePath.IsEmpty() && !bCaptureRequested && RenderedTicks >= CaptureFrame)
    {
        bCaptureRequested = true;
        FScreenshotRequest::RequestScreenshot(CapturePath, false, false);
    }
    if (!CapturePath.IsEmpty() && RenderedTicks > CaptureFrame + 300)
    {
        UE_LOG(LogTemp, Error, TEXT("UEFogLab screenshot timed out."));
        FPlatformMisc::RequestExitWithStatus(false, 2);
    }
}

void AFogLabGameMode::OnCaptureProcessed()
{
    FScreenshotRequest::OnScreenshotRequestProcessed().Remove(CaptureHandle);
    UE_LOG(LogTemp, Display, TEXT("UEFogLab screenshot processed: %s"), *CapturePath);
    FPlatformMisc::RequestExit(false);
}
