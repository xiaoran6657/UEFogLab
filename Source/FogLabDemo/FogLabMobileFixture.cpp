#include "FogLabMobileFixture.h"
#include "UEFogMobileStartup.h"
#include "UEFogMobileProbe.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Dom/JsonObject.h"

bool StartFogLabMobileFixture(UWorld* World)
{
    FString Mode;
    if (!FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileProbe="), Mode)) return false;
#if PLATFORM_ANDROID
    float Near = 400;
    FParse::Value(FCommandLine::Get(), TEXT("FogLabProbeNear="), Near);
    checkf(Near == 400 || Near == 600, TEXT("Frozen probe near distances are 400 or 600 cm"));
    UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
    UMaterial* Material = LoadObject<UMaterial>(nullptr, TEXT("/Engine/EngineDebugMaterials/LevelColorationUnlitMaterial.LevelColorationUnlitMaterial"));
    check(Cube && Material);
    auto J = MakeShared<FJsonObject>();
    J->SetStringField(TEXT("name"), TEXT("MobilePlanesV1")); J->SetNumberField(TEXT("nearCm"), Near);
    J->SetNumberField(TEXT("farCm"), 1200);
    J->SetStringField(TEXT("material"), Material->GetPathName());
    TArray<FMaterialParameterInfo> Infos; TArray<FGuid> Guids; Material->GetAllVectorParameterInfo(Infos, Guids);
    TArray<TSharedPtr<FJsonValue>> Parameters;
    for (const auto& Info : Infos) Parameters.Add(MakeShared<FJsonValueString>(Info.Name.ToString()));
    J->SetArrayField(TEXT("materialVectorParameters"), Parameters);
    J->SetNumberField(TEXT("shadingModels"), Material->GetShadingModels().GetShadingModelField());
    J->SetNumberField(TEXT("blendMode"), int32(Material->GetBlendMode()));
    check(Material->GetBlendMode() == BLEND_Opaque && Material->GetShadingModels().HasShadingModel(MSM_Unlit));
    auto* MID = UMaterialInstanceDynamic::Create(Material, World);
    MID->SetVectorParameterValue(TEXT("Color"), FLinearColor(.25, .5, 2, 1));
    auto Box = [&](FVector P, FVector S)
    {
        auto* A = World->SpawnActor<AStaticMeshActor>(P, FRotator::ZeroRotator);
        A->GetStaticMeshComponent()->SetMobility(EComponentMobility::Movable);
        A->GetStaticMeshComponent()->SetStaticMesh(Cube); A->SetActorScale3D(S);
        A->GetStaticMeshComponent()->SetMaterial(0, MID);
    };
    // Unit Cube is 100 cm across: front surfaces are precisely Near and 1200.
    Box(FVector(Near+1, -120, 0), FVector(.02, 2.4, 1.2));
    Box(FVector(1201, 350, 0), FVector(.02, 7, 2.4));
    auto* Camera = World->SpawnActor<ACameraActor>(FVector::ZeroVector, FRotator::ZeroRotator);
    Camera->GetCameraComponent()->SetFieldOfView(60); Camera->GetCameraComponent()->bConstrainAspectRatio = false;
    if (auto* PC = World->GetFirstPlayerController()) PC->SetViewTarget(Camera);
    auto* Exposure = World->SpawnActor<APostProcessVolume>(); Exposure->bUnbound = true;
    Exposure->Settings.bOverride_AutoExposureMethod = true; Exposure->Settings.AutoExposureMethod = EAutoExposureMethod::AEM_Manual;
    Exposure->Settings.bOverride_AutoExposureApplyPhysicalCameraExposure = true; Exposure->Settings.AutoExposureApplyPhysicalCameraExposure = false;
    Exposure->Settings.bOverride_BloomIntensity = true; Exposure->Settings.BloomIntensity = 0;
    UEFogMobileStartupSceneReady(World, Cube, Camera);
    UEFogMobileProbeFixtureReady(World, J);
    return true;
#else
    return false;
#endif
}
