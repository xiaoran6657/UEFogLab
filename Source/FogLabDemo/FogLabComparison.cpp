#include "FogLabComparison.h"
#include "UEFogCompare.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/LocalFogVolumeComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/PointLight.h"
#include "Engine/LocalFogVolume.h"
#include "Engine/ExponentialHeightFog.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Engine/GameViewportClient.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "ImageUtils.h"
#include "UnrealClient.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "EngineUtils.h"

namespace
{
TArray<TSharedPtr<FJsonValue>> V(const FVector& X)
{ return {MakeShared<FJsonValueNumber>(X.X), MakeShared<FJsonValueNumber>(X.Y), MakeShared<FJsonValueNumber>(X.Z)}; }
TSharedPtr<FJsonObject> Component(const USceneComponent* C)
{
    auto J = MakeShared<FJsonObject>();
    J->SetBoolField(TEXT("registered"), C->IsRegistered());
    J->SetBoolField(TEXT("visible"), C->IsVisible());
    J->SetArrayField(TEXT("positionCm"), V(C->GetComponentLocation()));
    J->SetArrayField(TEXT("scale"), V(C->GetComponentScale()));
    return J;
}
TSharedPtr<FJsonObject> Light(const ULightComponent* C)
{
    auto J = Component(C);
    J->SetArrayField(TEXT("photonDirection"), V(C->GetDirection()));
    const auto Color = C->GetLightColor();
    J->SetArrayField(TEXT("linearColor"), V(FVector(Color.R, Color.G, Color.B)));
    J->SetNumberField(TEXT("intensity"), C->Intensity);
    J->SetNumberField(TEXT("volumetricIntensity"), C->VolumetricScatteringIntensity);
    J->SetBoolField(TEXT("castShadows"), C->CastShadows);
    J->SetBoolField(TEXT("castVolumetricShadow"), C->bCastVolumetricShadow);
    return J;
}
}

void FFogLabComparison::Start(UWorld* World)
{
    Scene = World;
    Started = FPlatformTime::Seconds();
    FString Error;
    if (!ApplyUEFogCompareCVars(Error))
    { UE_LOG(LogTemp, Error, TEXT("Comparison rejected: %s"), *Error); FPlatformMisc::RequestExitWithStatus(false, 2); return; }
    auto R = GetUEFogCompareRequest();
    const FString Mode = R->GetStringField(TEXT("mode"));
    UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
    check(Cube);
    auto AddCube = [this, World, Cube](FVector P, FVector S)
    {
        auto* A = World->SpawnActor<AStaticMeshActor>(P, FRotator::ZeroRotator);
        A->GetStaticMeshComponent()->SetMobility(EComponentMobility::Movable);
        A->GetStaticMeshComponent()->SetStaticMesh(Cube);
        A->SetActorScale3D(S);
        Geometry.Add(A);
    };
    AddCube(FVector(200,0,-35), FVector(24,20,.5));
    AddCube(FVector(900,0,280), FVector(.3,18,6));
    for (int I = -3; I <= 3; ++I) AddCube(FVector(250,I*190,150), FVector(.6,.6,3));
    for (int I = -2; I <= 2; ++I) AddCube(FVector(-150,I*85,125), FVector(.06,.06,2.5));
    const FVector Photon = FVector(-1,-1,.15).GetSafeNormal();
    Sun = World->SpawnActor<ADirectionalLight>(FVector(0,0,600), Photon.Rotation());
    auto* S = Sun->GetLightComponent();
    S->SetMobility(EComponentMobility::Movable);
    S->SetIntensity(3);
    S->SetWorldRotation(Photon.Rotation());
    S->SetLightColor(FLinearColor(1,.75,.5), true);
    S->SetVolumetricScatteringIntensity(1);
    S->SetCastShadows(false); S->SetCastVolumetricShadow(false);
    Fill = World->SpawnActor<APointLight>(FVector(-450,-350,400), FRotator::ZeroRotator);
    auto* F = Fill->GetLightComponent();
    F->SetMobility(EComponentMobility::Movable);
    F->SetIntensity(25000); F->SetLightColor(FLinearColor::White, false);
    F->SetVolumetricScatteringIntensity(0);
    F->SetCastShadows(false); F->SetCastVolumetricShadow(false);
    CastChecked<UPointLightComponent>(F)->SetAttenuationRadius(2200);
    if (Mode.StartsWith(TEXT("Native")))
    {
        Local = World->SpawnActor<ALocalFogVolume>(FVector(300,0,180), FRotator::ZeroRotator);
        Local->SetActorScale3D(FVector(R->GetNumberField(TEXT("scaleX")), R->GetNumberField(TEXT("scaleY")), R->GetNumberField(TEXT("scaleZ"))));
        auto* L = Local->GetComponent();
        L->SetRadialFogExtinction(R->GetBoolField(TEXT("zeroLocal")) ? 0 : R->GetNumberField(TEXT("nativeDensity")));
        L->SetHeightFogExtinction(R->GetBoolField(TEXT("zeroLocal")) ? 0 : 8);
        L->SetHeightFogFalloff(1); L->SetHeightFogOffset(0);
        L->SetFogAlbedo(FLinearColor(.8f,.8f,.8f));
        L->SetFogEmissive(FLinearColor::Black); L->SetFogPhaseG(.5); L->SetFogStartDistance(0);
    }
    if (Mode == TEXT("NativeVF"))
    {
        Container = World->SpawnActor<AExponentialHeightFog>();
        auto* C = Container->GetComponent();
        C->SetFogDensity(1e-6f); C->SetSecondFogDensity(0); C->SetFogMaxOpacity(1e-4f);
        C->SetVolumetricFog(true); C->SetVolumetricFogExtinctionScale(0);
        C->SetVolumetricFogScatteringDistribution(.5); C->SetVolumetricFogDistance(6000);
        C->SetVolumetricFogStartDistance(0); C->SetVolumetricFogNearFadeInDistance(0);
        C->SetVolumetricFogAlbedo(FColor::White); C->SetVolumetricFogEmissive(FLinearColor::Black);
    }
    auto* E = World->SpawnActor<APostProcessVolume>();
    E->bUnbound = true;
    E->Settings.bOverride_AutoExposureMethod = true;
    E->Settings.AutoExposureMethod = EAutoExposureMethod::AEM_Manual;
    E->Settings.bOverride_AutoExposureApplyPhysicalCameraExposure = true;
    E->Settings.AutoExposureApplyPhysicalCameraExposure = false;
    E->Settings.bOverride_AutoExposureBias = true; E->Settings.AutoExposureBias = 0;
    FVector Eye(-850,-950,310), Target(280,0,145);
    if (R->GetStringField(TEXT("camera")) == TEXT("B")) { Eye = FVector(-350,-950,600); Target = FVector(300,0,180); }
    Camera = World->SpawnActor<ACameraActor>(Eye, (Target-Eye).Rotation());
    Camera->GetCameraComponent()->SetFieldOfView(60);
    Camera->GetCameraComponent()->bConstrainAspectRatio = false;
    World->GetFirstPlayerController()->SetViewTarget(Camera.Get());
    Handle = UGameViewportClient::OnScreenshotCaptured().AddSP(AsShared(), &FFogLabComparison::Capture);
}

void FFogLabComparison::Tick()
{
    auto R = GetUEFogCompareRequest();
    auto Vw = GetUEFogCompareView();
    // RHI end-frame and game-view counters can be offset. Cover the capture
    // boundary, then select the sole GPU profile containing ScreenshotWitness.
    if (Vw && ProfilesRequested < 8 && Vw->GetNumberField(TEXT("renderedViewIndex")) >= R->GetNumberField(TEXT("frames")) - 2 &&
        (!bCaptured || Vw->GetNumberField(TEXT("renderedViewIndex")) <= CapturedView + 2))
    {
        GEngine->Exec(Scene.Get(), TEXT("ProfileGPU"));
        ++ProfilesRequested;
    }
    if (!bRequested && Vw && Vw->GetNumberField(TEXT("renderedViewIndex")) >= R->GetNumberField(TEXT("frames")))
    {
        bRequested = true;
        ArmUEFogCompareScreenshot();
        FScreenshotRequest::RequestScreenshot(FPaths::Combine(R->GetStringField(TEXT("output")), TEXT("frame.png")), false, false);
    }
    if (bCaptured && Vw && Vw->GetNumberField(TEXT("renderedViewIndex")) >= CapturedView + 10)
        FPlatformMisc::RequestExitWithStatus(false, 0);
    if (FPlatformTime::Seconds() - Started > 180)
    { UE_LOG(LogTemp, Error, TEXT("Comparison screenshot timed out")); FPlatformMisc::RequestExitWithStatus(false, 4); }
}

TSharedPtr<FJsonObject> FFogLabComparison::ActorSnapshot() const
{
    auto J = MakeShared<FJsonObject>();
    int LFVs = 0, EHFs = 0;
    for (TActorIterator<ALocalFogVolume> I(Scene.Get()); I; ++I) ++LFVs;
    for (TActorIterator<AExponentialHeightFog> I(Scene.Get()); I; ++I) ++EHFs;
    J->SetNumberField(TEXT("localFogActorCount"), LFVs); J->SetNumberField(TEXT("heightFogActorCount"), EHFs);
    TArray<TSharedPtr<FJsonValue>> G;
    for (const auto& A : Geometry)
    {
        auto X = MakeShared<FJsonObject>();
        X->SetArrayField(TEXT("positionCm"), V(A->GetActorLocation())); X->SetArrayField(TEXT("scale"), V(A->GetActorScale3D()));
        G.Add(MakeShared<FJsonValueObject>(X));
    }
    J->SetArrayField(TEXT("geometry"), G);
    J->SetObjectField(TEXT("sun"), Light(Sun->GetLightComponent()));
    J->SetObjectField(TEXT("fill"), Light(Fill->GetLightComponent()));
    J->SetNumberField(TEXT("fillAttenuationCm"), CastChecked<UPointLightComponent>(Fill->GetLightComponent())->AttenuationRadius);
    auto C = Component(Camera->GetCameraComponent());
    C->SetNumberField(TEXT("fovDegrees"), Camera->GetCameraComponent()->FieldOfView);
    C->SetBoolField(TEXT("constrainAspectRatio"), Camera->GetCameraComponent()->bConstrainAspectRatio);
    J->SetObjectField(TEXT("camera"), C);
    if (Local.IsValid())
    {
        const auto* L = Local->GetComponent(); auto X = Component(L);
        X->SetNumberField(TEXT("radialExtinction"), L->RadialFogExtinction);
        X->SetNumberField(TEXT("heightExtinction"), L->HeightFogExtinction);
        X->SetNumberField(TEXT("heightFalloff"), L->HeightFogFalloff);
        X->SetNumberField(TEXT("heightOffset"), L->HeightFogOffset);
        X->SetNumberField(TEXT("worldRadiusCm"), L->GetComponentScale().GetMax()*500);
        X->SetNumberField(TEXT("g"), L->FogPhaseG); X->SetNumberField(TEXT("startDistanceCm"), L->FogStartDistance);
        X->SetArrayField(TEXT("albedoLinear"), V(FVector(L->FogAlbedo.R,L->FogAlbedo.G,L->FogAlbedo.B)));
        X->SetArrayField(TEXT("emissiveLinear"), V(FVector(L->FogEmissive.R,L->FogEmissive.G,L->FogEmissive.B)));
        J->SetObjectField(TEXT("lfv"), X);
    }
    if (Container.IsValid())
    {
        const auto* E = Container->GetComponent(); auto X = Component(E);
        X->SetNumberField(TEXT("fogDensity"), E->FogDensity); X->SetNumberField(TEXT("secondFogDensity"), E->SecondFogData.FogDensity);
        X->SetNumberField(TEXT("fogMaxOpacity"), E->FogMaxOpacity); X->SetBoolField(TEXT("volumetricEnabled"), E->bEnableVolumetricFog);
        X->SetNumberField(TEXT("extinctionScale"), E->VolumetricFogExtinctionScale); X->SetNumberField(TEXT("distanceCm"), E->VolumetricFogDistance);
        X->SetNumberField(TEXT("startDistanceCm"), E->VolumetricFogStartDistance); X->SetNumberField(TEXT("nearFadeCm"), E->VolumetricFogNearFadeInDistance);
        X->SetNumberField(TEXT("g"), E->VolumetricFogScatteringDistribution);
        J->SetObjectField(TEXT("ehfContainer"), X);
    }
    J->SetStringField(TEXT("exposureMethod"), TEXT("manual; physical camera exposure off; bias 0"));
    return J;
}

void FFogLabComparison::Capture(int32 Width, int32 Height, const TArray<FColor>& Pixels)
{
    UGameViewportClient::OnScreenshotCaptured().Remove(Handle);
    auto R = GetUEFogCompareRequest(); auto J = MakeShared<FJsonObject>();
    J->SetStringField(TEXT("kind"), TEXT("UEFogLabComparisonSnapshot")); J->SetNumberField(TEXT("schemaVersion"), 1);
    J->SetObjectField(TEXT("request"), R); J->SetObjectField(TEXT("view"), GetUEFogCompareScreenshotView());
    J->SetObjectField(TEXT("actors"), ActorSnapshot()); J->SetNumberField(TEXT("imageWidth"), Width); J->SetNumberField(TEXT("imageHeight"), Height);
    const FString Out = R->GetStringField(TEXT("output")); IFileManager::Get().MakeDirectory(*Out, true);
    TArray64<uint8> PNG; FImageUtils::PNGCompressImageArray(Width, Height, Pixels, PNG);
    FString Text; FJsonSerializer::Serialize(J, TJsonWriterFactory<>::Create(&Text));
    bool OK = FFileHelper::SaveArrayToFile(PNG, *FPaths::Combine(Out, TEXT("frame.png"))) &&
        FFileHelper::SaveStringToFile(Text, *FPaths::Combine(Out, TEXT("snapshot.json")), FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    if (!OK) { FPlatformMisc::RequestExitWithStatus(false, 3); return; }
    CapturedView = GetUEFogCompareScreenshotView()->GetNumberField(TEXT("renderedViewIndex"));
    bCaptured = true;
    UE_LOG(LogTemp, Display, TEXT("UEFogComparison screenshot engineFrame=%u view=%llu"), uint32(GetUEFogCompareScreenshotView()->GetNumberField(TEXT("engineFrame"))), CapturedView);
}
