#include "UEFogMobileStartup.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

#if PLATFORM_ANDROID
#include "SceneViewExtension.h"
#include "SceneView.h"
#include "DynamicRHI.h"
#include "RHIStrings.h"
#include "UnrealClient.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/GameModeBase.h"
#include "EngineUtils.h"
#include "DeviceProfiles/DeviceProfile.h"
#include "DeviceProfiles/DeviceProfileManager.h"
#include "HAL/IConsoleManager.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Misc/App.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/ScopeLock.h"
#include "ImageUtils.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include <atomic>
#include <cstdio>
#include <unistd.h>

namespace
{
struct FStartupState
{
    FString RunId, BuildId, Source, Directory, PhysicalDirectory;
    uint64 StartTicks = 0;
    double ClockTicks = 0, ReadyTime = 0;
    std::atomic<bool> Ready{false};
    std::atomic<uint64> Frames{0};
    const FSceneInterface* Scene = nullptr;
    uint32 LastFamilyFrame = MAX_uint32;
    FCriticalSection EventLock, ViewLock;
    TSharedPtr<FJsonObject> LastView;
    bool bCaptureRequested = false;
    FDelegateHandle CaptureHandle;
};
TUniquePtr<FStartupState> State;

TSharedRef<FJsonObject> Event(const TCHAR* Name)
{
    auto J = MakeShared<FJsonObject>();
    J->SetStringField(TEXT("kind"), TEXT("UEFogLab.AndroidStartupWitness"));
    J->SetNumberField(TEXT("schemaVersion"), 1);
    J->SetStringField(TEXT("event"), Name);
    J->SetStringField(TEXT("runId"), State->RunId);
    J->SetStringField(TEXT("declaredBuildId"), State->BuildId);
    J->SetStringField(TEXT("declaredSourceRevision"), State->Source);
    J->SetNumberField(TEXT("pid"), FPlatformProcess::GetCurrentProcessId());
    J->SetNumberField(TEXT("processStartTicks"), double(State->StartTicks));
    J->SetNumberField(TEXT("kernelClockTicksPerSecond"), State->ClockTicks);
    J->SetNumberField(TEXT("monotonicSeconds"), FPlatformTime::Seconds());
    J->SetBoolField(TEXT("hasFocus"), FApp::HasFocus());
    return J;
}

void Emit(const TSharedRef<FJsonObject>& J)
{
    FString Text; FJsonSerializer::Serialize(J, TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text));
    FScopeLock Guard(&State->EventLock);
    const bool OK = FFileHelper::SaveStringToFile(Text+TEXT("\n"), *FPaths::Combine(State->Directory, TEXT("events.jsonl")),
        FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM, &IFileManager::Get(), FILEWRITE_Append);
    UE_LOG(LogTemp, Display, TEXT("UEFogMobileStartup %s"), *Text);
    if (!OK) UE_LOG(LogTemp, Error, TEXT("UEFogMobileStartup could not persist own event log."));
}

int32 CVar(const TCHAR* Name)
{
    IConsoleVariable* V = IConsoleManager::Get().FindConsoleVariable(Name);
    return V ? V->GetInt() : -1;
}

TArray<TSharedPtr<FJsonValue>> XY(int32 X, int32 Y)
{
    return {MakeShared<FJsonValueNumber>(X), MakeShared<FJsonValueNumber>(Y)};
}

class FStartupExtension final : public FSceneViewExtensionBase
{
public:
    explicit FStartupExtension(const FAutoRegister& Register) : FSceneViewExtensionBase(Register) {}
    virtual void SetupViewFamily(FSceneViewFamily&) override {}
    virtual void PostRenderViewFamily_RenderThread(FRDGBuilder&, FSceneViewFamily& Family) override
    {
        if (!State || !State->Ready.load(std::memory_order_acquire) || Family.Scene != State->Scene ||
            Family.Views.Num() != 1 || !Family.Views[0]->bIsGameView || Family.FrameNumber == State->LastFamilyFrame) return;
        State->LastFamilyFrame = Family.FrameNumber;
        const FSceneView& View = *Family.Views[0];
        const uint64 Count = State->Frames.fetch_add(1)+1;
        auto J = Event(TEXT("view"));
        J->SetNumberField(TEXT("renderedViewFamilies"), double(Count));
        J->SetNumberField(TEXT("engineFamilyFrame"), Family.FrameNumber);
        J->SetStringField(TEXT("counterMeaning"), TEXT("Distinct game-view families at PostRenderViewFamily; CPU render-thread observation, not GPU completion"));
        J->SetStringField(TEXT("rhi"), GDynamicRHI ? GDynamicRHI->GetName() : TEXT("none"));
        J->SetStringField(TEXT("featureLevel"), View.GetFeatureLevel() == ERHIFeatureLevel::ES3_1 ? TEXT("ES3_1") : TEXT("other"));
        J->SetStringField(TEXT("shaderFormat"), LegacyShaderPlatformToShaderFormat(View.GetShaderPlatform()).ToString());
        J->SetArrayField(TEXT("unscaledViewMin"), XY(View.UnscaledViewRect.Min.X, View.UnscaledViewRect.Min.Y));
        J->SetArrayField(TEXT("unscaledViewSize"), XY(View.UnscaledViewRect.Width(), View.UnscaledViewRect.Height()));
        if (Family.RenderTarget)
        {
            const FIntPoint Size = Family.RenderTarget->GetSizeXY();
            J->SetArrayField(TEXT("viewFamilyRenderTargetSize"), XY(Size.X, Size.Y));
        }
        J->SetStringField(TEXT("sceneTextureExtent"), TEXT("not observed in PRE-02; resource descriptors belong to PRE-03"));
        auto Values = MakeShared<FJsonObject>();
        for (const TCHAR* Name : {TEXT("r.Mobile.ShadingPath"), TEXT("r.MobileHDR"), TEXT("r.Mobile.AntiAliasing"),
            TEXT("r.MSAACount"), TEXT("r.DynamicRes.OperationMode"), TEXT("r.UEFog.Enabled"), TEXT("r.Fog"),
            TEXT("r.VolumetricFog"), TEXT("r.LocalFogVolume"), TEXT("r.SupportLocalFogVolumes")})
            Values->SetNumberField(Name, CVar(Name));
        J->SetObjectField(TEXT("cvars"), Values);
        { FScopeLock Guard(&State->ViewLock); State->LastView = J; }
        if (Count == 1 || Count % 120 == 0) Emit(J);
    }
};

void Capture(int32 Width, int32 Height, const TArray<FColor>& Pixels)
{
    if (!State || !State->bCaptureRequested) return;
    UGameViewportClient::OnScreenshotCaptured().Remove(State->CaptureHandle);
    TArray64<uint8> PNG; FImageUtils::PNGCompressImageArray(Width, Height, Pixels, PNG);
    const bool OK = FFileHelper::SaveArrayToFile(PNG, *FPaths::Combine(State->Directory, TEXT("frame.png")));
    auto J = Event(TEXT("capture-complete"));
    J->SetBoolField(TEXT("saved"), OK);
    J->SetNumberField(TEXT("width"), Width); J->SetNumberField(TEXT("height"), Height);
    J->SetNumberField(TEXT("imageBytes"), PNG.Num());
    J->SetStringField(TEXT("physicalImagePath"), FPaths::Combine(State->PhysicalDirectory, TEXT("frame.png")));
    J->SetNumberField(TEXT("secondsAfterSceneReady"), FPlatformTime::Seconds()-State->ReadyTime);
    { FScopeLock Guard(&State->ViewLock); if (State->LastView) J->SetObjectField(TEXT("lastObservedView"), State->LastView); }
    Emit(J);
}
}
#endif

bool ApplyUEFogMobileStartupLaunch(FString& Error)
{
    FString RunId;
    if (!FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileRun="), RunId)) return true;
#if PLATFORM_ANDROID
    if (RunId.Len() != 32) { Error = TEXT("Invalid mobile runId"); return false; }
    for (TCHAR C : RunId) if (!FChar::IsHexDigit(C)) { Error = TEXT("Mobile runId must be 32 hex characters"); return false; }
    if (FString(FCommandLine::Get()).Contains(TEXT("FogLabCompareMode=")) || FString(FCommandLine::Get()).Contains(TEXT("FogLabCapture=")))
    { Error = TEXT("Mobile startup witness cannot mix Compare/legacy capture"); return false; }
    State = MakeUnique<FStartupState>(); State->RunId = RunId;
    if (!FParse::Value(FCommandLine::Get(), TEXT("FogLabBaselineBuild="), State->BuildId) ||
        !FParse::Value(FCommandLine::Get(), TEXT("FogLabBaselineSource="), State->Source) || State->Source.Len() != 40)
    { Error = TEXT("Mobile startup requires packaged build/source identity"); State.Reset(); return false; }
    if (!FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileOutput="), State->Directory) ||
        !State->Directory.StartsWith(TEXT("/data/user/")) || State->Directory.Contains(TEXT("..")) ||
        !State->Directory.EndsWith(TEXT("/com.uefoglab.mobilebaseline/files/M4-PRE-02/")+RunId))
    { Error = TEXT("Mobile output must be the dedicated app's private per-run files directory"); State.Reset(); return false; }
    IFileManager::Get().MakeDirectory(*State->Directory, true);
    State->PhysicalDirectory = IFileManager::Get().ConvertToAbsolutePathForExternalAppForWrite(*State->Directory);
    State->ClockTicks = double(sysconf(_SC_CLK_TCK));
    if (FILE* File = fopen("/proc/self/stat", "r"))
    {
        char Buffer[2048] = {}; if (fgets(Buffer, sizeof(Buffer), File))
        {
            FString Text = UTF8_TO_TCHAR(Buffer); int32 End = 0;
            if (Text.FindLastChar(TEXT(')'), End))
            {
                TArray<FString> Fields; Text.Mid(End+2).ParseIntoArrayWS(Fields);
                if (Fields.Num() > 19) State->StartTicks = FCString::Strtoui64(*Fields[19], nullptr, 10);
            }
        }
        fclose(File);
    }
    auto J = Event(TEXT("launch")); J->SetStringField(TEXT("commandLine"), FCommandLine::Get());
    J->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
    J->SetStringField(TEXT("physicalDirectory"), State->PhysicalDirectory); Emit(J);
    return true;
#else
    Error = TEXT("Mobile startup witness is Android-only"); return false;
#endif
}

TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> CreateUEFogMobileStartupExtension()
{
#if PLATFORM_ANDROID
    if (State) return FSceneViewExtensions::NewExtension<FStartupExtension>();
#endif
    return nullptr;
}

void UEFogMobileStartupSceneReady(UWorld* World, UStaticMesh* Cube, AActor* Camera)
{
#if PLATFORM_ANDROID
    if (!State) return;
    State->Scene = World->Scene; State->ReadyTime = FPlatformTime::Seconds();
    auto J = Event(TEXT("scene-ready"));
    J->SetStringField(TEXT("map"), World->GetPackage()->GetName());
    J->SetStringField(TEXT("gameMode"), World->GetAuthGameMode()->GetClass()->GetPathName());
    J->SetStringField(TEXT("cube"), Cube->GetPathName());
    int32 MeshCount = 0; for (TActorIterator<AStaticMeshActor> It(World); It; ++It) ++MeshCount;
    J->SetNumberField(TEXT("staticMeshActors"), MeshCount);
    J->SetStringField(TEXT("deviceProfile"), UDeviceProfileManager::Get().GetActiveProfile()->GetName());
    J->SetStringField(TEXT("cameraLocationCm"), Camera->GetActorLocation().ToString());
    Emit(J);
    State->CaptureHandle = UGameViewportClient::OnScreenshotCaptured().AddStatic(&Capture);
    if (GEngine) GEngine->AddOnScreenDebugMessage(72402, 240.0f, FColor::Green, TEXT("M4 PRE-02 run ")+State->RunId);
    State->Ready.store(true, std::memory_order_release);
#endif
}

void TickUEFogMobileStartup()
{
#if PLATFORM_ANDROID
    if (State && State->Ready.load() && !State->bCaptureRequested && FPlatformTime::Seconds()-State->ReadyTime >= 30.0 && State->Frames.load() >= 120)
    {
        State->bCaptureRequested = true;
        Emit(Event(TEXT("capture-requested")));
        FScreenshotRequest::RequestScreenshot(TEXT("M4StartupWitness.png"), false, false);
    }
#endif
}

void ShutdownUEFogMobileStartup()
{
#if PLATFORM_ANDROID
    if (State) { State->Ready.store(false); UGameViewportClient::OnScreenshotCaptured().Remove(State->CaptureHandle); }
#endif
}
