#include "FogLabMobileReconstructionFixture.h"
#include "UEFogMobileCoverageFixture.h"
#include "UEFogMobileReconstruction.h"
#include "UEFogMobileStartup.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
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
    FString Profile;if(!FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileRecon="),Profile)&&!FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileOpt="),Profile)&&!FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileLoad="),Profile)&&!FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileRepair="),Profile))return false;
#if PLATFORM_ANDROID
    FString RepairProfile;const bool Repair=FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileRepair="),RepairProfile);
    int32 Near=600;FString Variant=TEXT("Planes");
    FParse::Value(FCommandLine::Get(),TEXT("FogLabReconNear="),Near);
    FParse::Value(FCommandLine::Get(),TEXT("FogLabReconFixture="),Variant);
    const FUEFogMobileCoverageLayout Coverage = UEFogMobileCoverageLayout(Variant);
    check((Near==400||Near==600)&&(Variant==TEXT("Planes")||Variant==TEXT("ThinStripe")||(Repair&&(Variant==TEXT("ThinStripeShifted")||Variant==TEXT("ThinStripeBeforeEntry")||(Near==600&&Coverage.StripeCount>0)))));
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
    if(Variant==TEXT("ThinStripe")||(Repair&&(Variant==TEXT("ThinStripeShifted")||Variant==TEXT("ThinStripeBeforeEntry"))))
    {
        // Frozen before GPU: actual baseline projection, NOT requested FOV.
        constexpr double AX=.700037113670545,AY=.32475948421789075;
        const double Depth=Variant==TEXT("ThinStripeShifted")?800:Variant==TEXT("ThinStripeBeforeEntry")?300:600;
        const double Column=Variant==TEXT("ThinStripeShifted")?1002:Variant==TEXT("ThinStripeBeforeEntry")?1004:1000;
        const double Y=Depth*AX*(2*(Column+.5)/1552-1);
        const double HalfY=Depth*AX*2*.3/1552,HalfZ=Depth*AY*2*60/720;
        Stripe=Box(FVector(Depth+.01,Y,0),FVector(.0002,2*HalfY/100,2*HalfZ/100));
    }
    UInstancedStaticMeshComponent* CoverageMesh = nullptr;
    if (Coverage.StripeCount > 0)
    {
        constexpr double AX = .700037113670545;
        constexpr double AY = .32475948421789075;
        constexpr double Depth = 600;
        const FVector Scale(.0002, (2 * Depth * AX * 2 * .3 / 1552) / 100,
                            (2 * Depth * AY * 2 * Coverage.HalfHeightRows / 720) / 100);
        AActor* Owner = World->SpawnActor<AActor>();
        CoverageMesh = NewObject<UInstancedStaticMeshComponent>(Owner);
        Owner->AddInstanceComponent(CoverageMesh);
        Owner->SetRootComponent(CoverageMesh);
        CoverageMesh->SetMobility(EComponentMobility::Movable);
        CoverageMesh->SetStaticMesh(Cube);
        CoverageMesh->SetMaterial(0, MID);
        CoverageMesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        CoverageMesh->SetCanEverAffectNavigation(false);
        TArray<FTransform> Instances;
        Instances.Reserve(Coverage.StripeCount);
        for (int32 Index = 0; Index < Coverage.StripeCount; ++Index)
        {
            const int32 Column = Coverage.FirstColumn + 2 * Index;
            const double Y = Depth * AX * (2 * (Column + .5) / 1552 - 1);
            Instances.Emplace(FQuat::Identity, FVector(Depth + .01, Y, 0), Scale);
        }
        CoverageMesh->AddInstances(Instances, false, false, false);
        CoverageMesh->RegisterComponent();
    }
    auto* Camera=World->SpawnActor<ACameraActor>(FVector::ZeroVector,FRotator::ZeroRotator);
    Camera->GetCameraComponent()->SetFieldOfView(60);Camera->GetCameraComponent()->bConstrainAspectRatio=false;
    if(auto* PC=World->GetFirstPlayerController())PC->SetViewTarget(Camera);
    auto* Exposure=World->SpawnActor<APostProcessVolume>();Exposure->bUnbound=true;
    Exposure->Settings.bOverride_AutoExposureMethod=true;Exposure->Settings.AutoExposureMethod=EAutoExposureMethod::AEM_Manual;
    Exposure->Settings.bOverride_AutoExposureApplyPhysicalCameraExposure=true;Exposure->Settings.AutoExposureApplyPhysicalCameraExposure=false;
    Exposure->Settings.bOverride_BloomIntensity=true;Exposure->Settings.BloomIntensity=0;
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("name"),FString(TEXT("MobileRecon"))+Variant+TEXT("V1"));
    J->SetNumberField(TEXT("nearCm"),Near);J->SetNumberField(TEXT("farCm"),1200);J->SetNumberField(TEXT("meshActors"),Stripe?3:2);
    J->SetStringField(TEXT("material"),Material->GetPathName());J->SetNumberField(TEXT("blendMode"),int32(Material->GetBlendMode()));
    J->SetNumberField(TEXT("shadingModels"),Material->GetShadingModels().GetShadingModelField());
    auto Array=[](FVector V){TArray<TSharedPtr<FJsonValue>> A;A.Add(MakeShared<FJsonValueNumber>(V.X));A.Add(MakeShared<FJsonValueNumber>(V.Y));A.Add(MakeShared<FJsonValueNumber>(V.Z));return A;};
    if(Stripe)
    {
        J->SetArrayField(TEXT("stripeActorLocationCm"),Array(Stripe->GetActorLocation()));J->SetArrayField(TEXT("stripeActorScale"),Array(Stripe->GetActorScale3D()));
    }
    if (CoverageMesh)
    {
        TArray<TSharedPtr<FJsonValue>> Transforms;
        const int32 InstanceCount = CoverageMesh->GetInstanceCount();
        for (int32 Index = 0; Index < InstanceCount; ++Index)
        {
            FTransform Actual;
            const bool bFound = CoverageMesh->GetInstanceTransform(Index, Actual, true);
            check(bFound);
            auto Transform = MakeShared<FJsonObject>();
            Transform->SetNumberField(TEXT("requestedColumn"), Coverage.FirstColumn + 2 * Index);
            Transform->SetArrayField(TEXT("locationCm"), Array(Actual.GetLocation()));
            Transform->SetArrayField(TEXT("scale"), Array(Actual.GetScale3D()));
            Transform->SetArrayField(TEXT("rotationEulerDegrees"), Array(Actual.GetRotation().Euler()));
            Transforms.Add(MakeShared<FJsonValueObject>(Transform));
        }
        J->SetNumberField(TEXT("coverageFixtureVersion"), 1);
        J->SetStringField(TEXT("coverageComponentClass"), CoverageMesh->GetClass()->GetName());
        J->SetStringField(TEXT("coverageMesh"), CoverageMesh->GetStaticMesh()->GetPathName());
        J->SetNumberField(TEXT("coverageInstanceCount"), InstanceCount);
        J->SetArrayField(TEXT("coverageTransforms"), Transforms);
    }
    UEFogMobileStartupSceneReady(World,Cube,Camera);UEFogMobileReconstructionFixtureReady(World,J);return true;
#else
    return false;
#endif
}
