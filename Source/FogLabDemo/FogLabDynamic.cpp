#include "FogLabComparison.h"
#include "UEFogCompare.h"
#include "UEFogDynamic.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/LocalFogVolume.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/StaticMesh.h"
#include "Engine/GameViewportClient.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Components/LightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "ImageUtils.h"
#include "UnrealClient.h"
#include "Misc/App.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"

namespace
{
void Fail(const TCHAR* Reason)
{
    UE_LOG(LogTemp, Error, TEXT("UEFog dynamic capture failed: %s"), Reason);
    FPlatformMisc::RequestExitWithStatus(false, 5);
}
}

void FFogLabComparison::InitializeDynamic()
{
    // These affect only the explicitly requested offline demonstration.
    FApp::SetUseFixedTimeStep(true);
    FApp::SetFixedDeltaTime(1.0 / 30.0);
    auto* A = Scene->SpawnActor<AStaticMeshActor>(FVector(-100,-500,160), FRotator::ZeroRotator);
    A->GetStaticMeshComponent()->SetMobility(EComponentMobility::Movable);
    A->GetStaticMeshComponent()->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube")));
    A->SetActorScale3D(FVector(.35,.8,3.2));
    MovingProp = A;
    Geometry.Add(A);
    UpdateDynamic(-1);
}

void FFogLabComparison::UpdateDynamic(int32 Index)
{
    const auto R = GetUEFogCompareRequest();
    const FString Shot = R->GetStringField(TEXT("dynamicShot"));
    const int32 Count = R->GetIntegerField(TEXT("dynamicFrames"));
    const int32 Sample = Index == -2 ? Count - 1 : FMath::Max(Index, 0);
    const double P = Sample / double(Count - 1);
    const double U = .5 - .5 * FMath::Cos(PI * P);
    FVector Eye(-850,-950,310), Target(280,0,145);
    FVector Sphere(300,0,180), Prop(-100,-500,160);
    FVector Photon = FVector(-1,-1,.15).GetSafeNormal();
    if (Shot == TEXT("Camera"))
    {
        const double Angle = FMath::DegreesToRadians(-20.0 + 40.0 * U);
        const FVector Offset = Eye - Target;
        Eye = Target + FVector(Offset.X * FMath::Cos(Angle) - Offset.Y * FMath::Sin(Angle),
            Offset.X * FMath::Sin(Angle) + Offset.Y * FMath::Cos(Angle), Offset.Z);
    }
    if (Shot == TEXT("Object") || Shot == TEXT("Reconstruction"))
        Prop = FVector(0,-250,160) + FVector(-.6435,.7655,0) * (-180 + 360 * U);
    if (Shot == TEXT("Reconstruction"))
    { Eye = FVector(-650,-800,260); Target = FVector(170,0,160); }
    if (Shot == TEXT("Fog")) Sphere.Y = -280 + 560 * U;
    if (Shot == TEXT("Light"))
    {
        const double Angle = FMath::DegreesToRadians(-75.0 + 150.0 * U);
        const FVector K = Photon;
        Photon = FVector(K.X * FMath::Cos(Angle) - K.Y * FMath::Sin(Angle),
            K.X * FMath::Sin(Angle) + K.Y * FMath::Cos(Angle), K.Z);
    }
    Camera->SetActorLocationAndRotation(Eye, (Target - Eye).Rotation());
    MovingProp->SetActorLocation(Prop);
    Sun->SetActorRotation(Photon.Rotation());
    if (Local.IsValid()) Local->SetActorLocation(Sphere);

    FUEFogDynamicState State;
    State.OutputIndex = Index; State.TimeSeconds = Sample / 30.0;
    State.Phase = P; State.GameFrame = GFrameCounter;
    State.SphereWorldCm = Sphere;
    // Explicit demo synchronization, not automatic extraction of arbitrary UE lighting.
    State.PhotonDirection = Sun->GetLightComponent()->GetDirection().GetSafeNormal();
    auto Actors = ActorSnapshot();
    Actors->SetNumberField(TEXT("deltaSeconds"), FApp::GetDeltaTime());
    Actors->SetNumberField(TEXT("worldTimeSeconds"), Scene->GetTimeSeconds());
    FJsonSerializer::Serialize(Actors, TJsonWriterFactory<>::Create(&State.ActorJson));
    PublishUEFogDynamicState(State);
}

void FFogLabComparison::TickDynamic()
{
    const auto R = GetUEFogCompareRequest();
    if (FPlatformTime::Seconds() - Started > 420) { Fail(TEXT("420-second shot deadline")); return; }
    if (bDynamicAwaiting) { Fail(TEXT("Previous rendered frame did not produce a screenshot")); return; }
    if (DynamicOutput == R->GetIntegerField(TEXT("dynamicFrames")))
    {
        // UE can submit one final view while processing an exit request. Hold
        // the endpoint, but disarm its output identity so Last stays unique.
        UpdateDynamic(-2);
        // Allow the final asynchronous ProfileGPU table/trace to complete.
        if (++DynamicTailTicks < 6) return;
        UGameViewportClient::OnScreenshotCaptured().Remove(Handle);
        FPlatformMisc::RequestExitWithStatus(false, 0);
        return;
    }
    ++DynamicTicks;
    const int32 LogicalOutput = DynamicTicks - R->GetIntegerField(TEXT("frames")) - 1;
    const int32 Count = R->GetIntegerField(TEXT("dynamicFrames"));
    if (FMath::Abs(LogicalOutput) <= 2 || FMath::Abs(LogicalOutput - Count / 2) <= 2 || FMath::Abs(LogicalOutput - (Count - 1)) <= 2)
        GEngine->Exec(Scene.Get(), TEXT("ProfileGPU"));
    if (DynamicTicks <= R->GetIntegerField(TEXT("frames"))) { UpdateDynamic(-1); return; }
    UpdateDynamic(DynamicOutput);
    ArmUEFogCompareScreenshot();
    bDynamicAwaiting = true;
    FScreenshotRequest::RequestScreenshot(TEXT("UEFogDynamic.png"), false, false);
}

void FFogLabComparison::CaptureDynamic(int32 Width, int32 Height, const TArray<FColor>& Pixels)
{
    const auto R = GetUEFogCompareRequest();
    const auto View = GetUEFogCompareScreenshotView();
    if (!bDynamicAwaiting || !View || !View->HasField(TEXT("dynamic")))
    { Fail(TEXT("Unarmed or unobserved screenshot")); return; }
    const auto D = View->GetObjectField(TEXT("dynamic"));
    const uint32 Frame = View->GetIntegerField(TEXT("engineFrame"));
    const uint64 ViewIndex = uint64(View->GetNumberField(TEXT("renderedViewIndex")));
    if (D->GetIntegerField(TEXT("outputIndex")) != DynamicOutput ||
        (DynamicOutput > 0 && (Frame != LastDynamicFrame + 1 || ViewIndex != LastDynamicView + 1)) ||
        Width != R->GetIntegerField(TEXT("width")) || Height != R->GetIntegerField(TEXT("height")))
    { Fail(TEXT("Screenshot/render-state mismatch or nonconsecutive scene views")); return; }
    const FString Name = FString::Printf(TEXT("frame-%05d.png"), DynamicOutput);
    TArray64<uint8> PNG;
    FImageUtils::PNGCompressImageArray(Width, Height, Pixels, PNG);
    if (!FFileHelper::SaveArrayToFile(PNG, *FPaths::Combine(R->GetStringField(TEXT("output")), Name)))
    { Fail(TEXT("PNG write failed")); return; }
    auto J = MakeShared<FJsonObject>();
    J->SetStringField(TEXT("image"), Name);
    J->SetNumberField(TEXT("outputIndex"), DynamicOutput);
    J->SetNumberField(TEXT("imageWidth"), Width); J->SetNumberField(TEXT("imageHeight"), Height);
    J->SetObjectField(TEXT("view"), View);
    DynamicFrames.Add(MakeShared<FJsonValueObject>(J));
    LastDynamicFrame = Frame; LastDynamicView = ViewIndex;
    ++DynamicOutput; bDynamicAwaiting = false;
    if (DynamicOutput == R->GetIntegerField(TEXT("dynamicFrames")))
    {
        auto Sequence = MakeShared<FJsonObject>();
        Sequence->SetStringField(TEXT("kind"), TEXT("UEFogLabDynamicSequence"));
        Sequence->SetNumberField(TEXT("schemaVersion"), 1);
        Sequence->SetObjectField(TEXT("request"), R);
        Sequence->SetNumberField(TEXT("warmupTicks"), R->GetIntegerField(TEXT("frames")));
        Sequence->SetArrayField(TEXT("frames"), DynamicFrames);
        FString Text; FJsonSerializer::Serialize(Sequence, TJsonWriterFactory<>::Create(&Text));
        if (!FFileHelper::SaveStringToFile(Text, *FPaths::Combine(R->GetStringField(TEXT("output")), TEXT("sequence.json")),
            FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) { Fail(TEXT("Sequence write failed")); return; }
        UE_LOG(LogTemp, Display, TEXT("UEFog dynamic sequence completed: %d consecutive frames"), DynamicOutput);
    }
}
