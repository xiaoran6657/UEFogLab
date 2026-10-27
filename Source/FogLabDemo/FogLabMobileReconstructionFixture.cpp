#include "FogLabMobileReconstructionFixture.h"
#include "UEFogMobileReconstruction.h"
#include "UEFogMobileStartup.h"
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

bool StartFogLabMobileReconstructionFixture(UWorld* World)
{
    FString Profile;if(!FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileRecon="),Profile))return false;
#if PLATFORM_ANDROID
    int32 Near=600;FString Variant=TEXT("Planes");
    FParse::Value(FCommandLine::Get(),TEXT("FogLabReconNear="),Near);
    FParse::Value(FCommandLine::Get(),TEXT("FogLabReconFixture="),Variant);
    check((Near==400||Near==600)&&(Variant==TEXT("Planes")||Variant==TEXT("ThinStripe")));
    auto* Cube=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));
    auto* Material=LoadObject<UMaterial>(nullptr,TEXT("/Engine/EngineDebugMaterials/LevelColorationUnlitMaterial.LevelColorationUnlitMaterial"));
    check(Cube&&Material&&Material->GetBlendMode()==BLEND_Opaque&&Material->GetShadingModels().HasShadingModel(MSM_Unlit));
    auto* MID=UMaterialInstanceDynamic::Create(Material,World);MID->SetVectorParameterValue(TEXT("Color"),FLinearColor(.25,.5,2,1));
    auto Box=[&](FVector Position,FVector Scale)
    {
        auto* A=World->SpawnActor<AStaticMeshActor>(Position,FRotator::ZeroRotator);
        A->GetStaticMeshComponent()->SetMobility(EComponentMobility::Movable);A->GetStaticMeshComponent()->SetStaticMesh(Cube);
        A->SetActorScale3D(Scale);A->GetStaticMeshComponent()->SetMaterial(0,MID);return A;
    };
    Box(FVector(Near+1,-120,0),FVector(.02,2.4,1.2));
    Box(FVector(1201,350,0),FVector(.02,7,2.4));
    AStaticMeshActor* Stripe=nullptr;
    if(Variant==TEXT("ThinStripe"))
    {
        // Frozen before GPU: actual baseline projection, NOT requested FOV.
        constexpr double AX=.700037113670545,AY=.32475948421789075;
        const double Y=600*AX*(2*(1000+.5)/1552-1);
        const double HalfY=600*AX*2*.3/1552,HalfZ=600*AY*2*60/720;
        Stripe=Box(FVector(600.01,Y,0),FVector(.0002,2*HalfY/100,2*HalfZ/100));
    }
    auto* Camera=World->SpawnActor<ACameraActor>(FVector::ZeroVector,FRotator::ZeroRotator);
    Camera->GetCameraComponent()->SetFieldOfView(60);Camera->GetCameraComponent()->bConstrainAspectRatio=false;
    if(auto* PC=World->GetFirstPlayerController())PC->SetViewTarget(Camera);
    auto* Exposure=World->SpawnActor<APostProcessVolume>();Exposure->bUnbound=true;
    Exposure->Settings.bOverride_AutoExposureMethod=true;Exposure->Settings.AutoExposureMethod=EAutoExposureMethod::AEM_Manual;
    Exposure->Settings.bOverride_AutoExposureApplyPhysicalCameraExposure=true;Exposure->Settings.AutoExposureApplyPhysicalCameraExposure=false;
    Exposure->Settings.bOverride_BloomIntensity=true;Exposure->Settings.BloomIntensity=0;
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("name"),Variant==TEXT("Planes")?TEXT("MobileReconPlanesV1"):TEXT("MobileReconThinStripeV1"));
    J->SetNumberField(TEXT("nearCm"),Near);J->SetNumberField(TEXT("farCm"),1200);J->SetNumberField(TEXT("meshActors"),Stripe?3:2);
    J->SetStringField(TEXT("material"),Material->GetPathName());J->SetNumberField(TEXT("blendMode"),int32(Material->GetBlendMode()));
    J->SetNumberField(TEXT("shadingModels"),Material->GetShadingModels().GetShadingModelField());
    if(Stripe)
    {
        auto Array=[](FVector V){TArray<TSharedPtr<FJsonValue>> A;A.Add(MakeShared<FJsonValueNumber>(V.X));A.Add(MakeShared<FJsonValueNumber>(V.Y));A.Add(MakeShared<FJsonValueNumber>(V.Z));return A;};
        J->SetArrayField(TEXT("stripeActorLocationCm"),Array(Stripe->GetActorLocation()));J->SetArrayField(TEXT("stripeActorScale"),Array(Stripe->GetActorScale3D()));
    }
    UEFogMobileStartupSceneReady(World,Cube,Camera);UEFogMobileReconstructionFixtureReady(World,J);return true;
#else
    return false;
#endif
}
