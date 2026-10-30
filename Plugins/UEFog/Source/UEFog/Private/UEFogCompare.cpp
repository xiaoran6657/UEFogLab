#include "UEFogCompare.h"
#include "UEFogDynamic.h"
#include "ScenePrivate.h"
#include "LocalFogVolumeSceneProxy.h"
#include "LightSceneInfo.h"
#include "LightSceneProxy.h"
#include "HAL/IConsoleManager.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/ScopeLock.h"
#include "Serialization/JsonSerializer.h"
#include "SceneView.h"
#include "SceneManagement.h"
#include "RenderGraphBuilder.h"
#include "RenderGraphEvent.h"
#include "ProfilingDebugging/RealtimeGPUProfiler.h"

namespace
{
TSharedPtr<FJsonObject> Request;
TSharedPtr<FJsonObject> LastView;
TSharedPtr<FJsonObject> ScreenshotView;
TAtomic<bool> ScreenshotArmed(false);
FCriticalSection ViewLock;
uint64 ViewCount = 0;
const TCHAR* ObservedCVars[] = {
    TEXT("r.UEFog.Enabled"), TEXT("r.UEFog.Steps"), TEXT("r.UEFog.Downsample"), TEXT("r.UEFog.Reconstruction"),
    TEXT("r.UEFog.LightingModel"), TEXT("r.UEFog.Albedo"), TEXT("r.UEFog.HGg"),
    TEXT("r.UEFog.IncidentR"), TEXT("r.UEFog.IncidentG"), TEXT("r.UEFog.IncidentB"),
    TEXT("r.UEFog.PhotonX"), TEXT("r.UEFog.PhotonY"), TEXT("r.UEFog.PhotonZ"),
    TEXT("r.UEFog.Extinction"), TEXT("r.UEFog.DensityMode"), TEXT("r.UEFog.EarlyOut"),
    TEXT("r.Fog"), TEXT("r.SupportLocalFogVolumes"), TEXT("r.LocalFogVolume"),
    TEXT("r.LocalFogVolume.RenderDuringHeightFogPass"), TEXT("r.LocalFogVolume.RenderIntoVolumetricFog"),
    TEXT("r.LocalFogVolume.GlobalStartDistance"), TEXT("r.LocalFogVolume.TilePixelSize"),
    TEXT("r.LocalFogVolume.TileDebug"), TEXT("r.LocalFogVolume.HalfResolution"),
    TEXT("r.LocalFogVolume.MaxDensityIntoVolumetricFog"), TEXT("r.VolumetricFog"),
    TEXT("r.VolumetricFog.GridPixelSize"), TEXT("r.VolumetricFog.GridSizeZ"),
    TEXT("r.VolumetricFog.TemporalReprojection"), TEXT("r.VolumetricFog.Jitter"), TEXT("r.VolumetricFog.HistoryWeight"),
    TEXT("sg.ShadowQuality"), TEXT("r.AntiAliasingMethod"), TEXT("r.ScreenPercentage"),
    TEXT("r.DynamicRes.OperationMode"), TEXT("r.EyeAdaptation.PreExposureOverride"),
    TEXT("r.DefaultFeature.AutoExposure"), TEXT("r.MotionBlurQuality"),
    TEXT("r.BloomQuality"), TEXT("r.DepthOfFieldQuality"), TEXT("r.OneFrameThreadLag")
};
TArray<TSharedPtr<FJsonValue>> N(std::initializer_list<double> Values)
{
    TArray<TSharedPtr<FJsonValue>> A;
    for (double X : Values) A.Add(MakeShared<FJsonValueNumber>(X));
    return A;
}
bool Set(const TCHAR* Name, double Value, FString& Error)
{
    IConsoleVariable* V = IConsoleManager::Get().FindConsoleVariable(Name);
    if (!V) { Error = FString::Printf(TEXT("Missing comparison CVar %s"), Name); return false; }
    V->Set(*FString::Printf(TEXT("%.17g"), Value), ECVF_SetByCommandline);
    if (!FMath::IsNearlyEqual(double(V->GetFloat()), Value, 1e-5))
    { Error = FString::Printf(TEXT("Comparison CVar did not apply: %s"), Name); return false; }
    return true;
}
}

bool IsUEFogCompareActive() { return Request.IsValid(); }
TSharedPtr<FJsonObject> GetUEFogCompareRequest() { return Request; }

bool ApplyUEFogCompareLaunch(FString& Error)
{
    FString Mode, Path;
    TArray<FString> Tokens, Switches;
    FCommandLine::Parse(FCommandLine::Get(), Tokens, Switches);
    int32 Modes = 0, Paths = 0;
    for (const FString& S : Switches)
    {
        if (S.StartsWith(TEXT("FogLabCompareMode="), ESearchCase::IgnoreCase)) ++Modes;
        if (S.StartsWith(TEXT("FogLabCompareRequest="), ESearchCase::IgnoreCase)) ++Paths;
    }
    if (Modes == 0 && Paths == 0) return true;
    if (Modes != 1 || Paths != 1) { Error = TEXT("Exactly one compare mode and request are required"); return false; }
    for (const FString& S : Switches)
    {
        // A comparison request exclusively owns the launch configuration.
        if ((S.StartsWith(TEXT("FogLab"), ESearchCase::IgnoreCase) && !S.StartsWith(TEXT("FogLabCompare"), ESearchCase::IgnoreCase)) ||
            S.StartsWith(TEXT("ExecCmds="), ESearchCase::IgnoreCase))
        { Error = TEXT("Conflicting legacy/ExecCmds comparison arguments"); return false; }
    }
    FParse::Value(FCommandLine::Get(), TEXT("FogLabCompareMode="), Mode);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabCompareRequest="), Path);
    FString Text;
    TSharedPtr<FJsonObject> R;
    if (!FFileHelper::LoadFileToString(Text, *Path) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), R))
    { Error = TEXT("Invalid comparison request JSON"); return false; }
    const TSet<FString> ModesAllowed = { TEXT("Off"), TEXT("Ours"), TEXT("NativeLFV"), TEXT("NativeVF") };
    FString RequestedMode, Profile, Camera, Output;
    double Width = 0, Height = 0, Frames = 0, Density = 0, SX = 0, SY = 0, SZ = 0;
    bool Zero = false;
    if (!R->TryGetStringField(TEXT("mode"), RequestedMode) || RequestedMode != Mode || !ModesAllowed.Contains(Mode) ||
        !R->TryGetStringField(TEXT("profile"), Profile) || !R->TryGetStringField(TEXT("camera"), Camera) ||
        !R->TryGetStringField(TEXT("output"), Output) || Output.IsEmpty() ||
        !R->TryGetBoolField(TEXT("zeroLocal"), Zero) ||
        !R->TryGetNumberField(TEXT("width"), Width) || !R->TryGetNumberField(TEXT("height"), Height) ||
        !R->TryGetNumberField(TEXT("frames"), Frames) || !R->TryGetNumberField(TEXT("nativeDensity"), Density) ||
        !R->TryGetNumberField(TEXT("scaleX"), SX) || !R->TryGetNumberField(TEXT("scaleY"), SY) || !R->TryGetNumberField(TEXT("scaleZ"), SZ))
    { Error = TEXT("Missing or inconsistent comparison fields"); return false; }
    const bool bValidProfile = Mode == TEXT("Ours") ?
        (Profile == TEXT("High") || Profile == TEXT("MediumB") || Profile == TEXT("MediumG") || Profile == TEXT("LowG")) :
        (Mode == TEXT("NativeVF") ? (Profile == TEXT("VFTR") || Profile == TEXT("VFNoTR")) : Profile == TEXT("Default"));
    if (!bValidProfile || (Camera != TEXT("A") && Camera != TEXT("B")) ||
        Width < 64 || Height < 64 || Width > 4096 || Height > 4096 || Width != FMath::FloorToDouble(Width) || Height != FMath::FloorToDouble(Height) ||
        Frames < 120 || Frames > 600 || Frames != FMath::FloorToDouble(Frames) ||
        !FMath::IsFinite(Density) || Density <= 0 || Density > 10 ||
        !FMath::IsNearlyEqual(SX, .72, 1e-8) || SX != SY || SX != SZ ||
        (Zero && Mode != TEXT("NativeLFV") && Mode != TEXT("NativeVF")))
    { Error = TEXT("Illegal comparison profile, dimensions, density or nonuniform scale"); return false; }
    int32 ResX = 0, ResY = 0;
    FParse::Value(FCommandLine::Get(), TEXT("ResX="), ResX);
    FParse::Value(FCommandLine::Get(), TEXT("ResY="), ResY);
    if (ResX != Width || ResY != Height) { Error = TEXT("Comparison viewport arguments do not match request"); return false; }
    if (R->HasField(TEXT("dynamicShot")))
    {
        FString Shot; double Count = 0, FPS = 0;
        const TSet<FString> Shots = {TEXT("Camera"), TEXT("Object"), TEXT("Fog"), TEXT("Light"), TEXT("Reconstruction")};
        if (!R->TryGetStringField(TEXT("dynamicShot"), Shot) || !Shots.Contains(Shot) ||
            !R->TryGetNumberField(TEXT("dynamicFrames"), Count) || Count < 3 || Count > 900 || Count != FMath::FloorToDouble(Count) ||
            !R->TryGetNumberField(TEXT("dynamicFps"), FPS) || FPS != 30 || Zero ||
            (Shot == TEXT("Reconstruction") && Mode != TEXT("Ours")))
        { Error = TEXT("Illegal dynamic shot, frame count, fps, mode or zero control"); return false; }
    }
    Request = R;
    return ApplyUEFogCompareCVars(Error);
}

bool ApplyUEFogCompareCVars(FString& Error)
{
    if (!Request) return true;
    const FString Mode = Request->GetStringField(TEXT("mode")), P = Request->GetStringField(TEXT("profile"));
    auto* Support = IConsoleManager::Get().FindConsoleVariable(TEXT("r.SupportLocalFogVolumes"));
    if (!Support || Support->GetInt() != 1) { Error = TEXT("Local fog volume support unavailable"); return false; }
    const bool Ours = Mode == TEXT("Ours"), LFV = Mode.StartsWith(TEXT("Native")), VF = Mode == TEXT("NativeVF");
    const TPair<const TCHAR*, double> Settings[] = {
        {TEXT("sg.ShadowQuality"), 3}, {TEXT("r.UEFog.Enabled"), Ours},
        {TEXT("r.UEFog.Steps"), P == TEXT("LowG") ? 32 : 64}, {TEXT("r.UEFog.Downsample"), P.StartsWith(TEXT("Medium")) || P == TEXT("LowG") ? 2 : 1},
        {TEXT("r.UEFog.Reconstruction"), P == TEXT("MediumB") ? 0 : 1},
        {TEXT("r.UEFog.LightingModel"), 1}, {TEXT("r.UEFog.Albedo"), .8}, {TEXT("r.UEFog.HGg"), .5},
        {TEXT("r.UEFog.IncidentR"), 4}, {TEXT("r.UEFog.IncidentG"), 3}, {TEXT("r.UEFog.IncidentB"), 2},
        {TEXT("r.UEFog.PhotonX"), -1}, {TEXT("r.UEFog.PhotonY"), -1}, {TEXT("r.UEFog.PhotonZ"), .15},
        {TEXT("r.UEFog.DensityMode"), 1}, {TEXT("r.UEFog.Extinction"), .35}, {TEXT("r.UEFog.EarlyOut"), 0},
        {TEXT("r.Fog"), 1}, {TEXT("r.LocalFogVolume"), LFV}, {TEXT("r.VolumetricFog"), VF},
        {TEXT("r.LocalFogVolume.RenderDuringHeightFogPass"), 0}, {TEXT("r.LocalFogVolume.RenderIntoVolumetricFog"), VF},
        {TEXT("r.LocalFogVolume.GlobalStartDistance"), 10}, {TEXT("r.LocalFogVolume.TilePixelSize"), 128},
        {TEXT("r.LocalFogVolume.TileDebug"), 0}, {TEXT("r.LocalFogVolume.HalfResolution"), 0},
        {TEXT("r.LocalFogVolume.MaxDensityIntoVolumetricFog"), .01},
        {TEXT("r.VolumetricFog.GridPixelSize"), 8}, {TEXT("r.VolumetricFog.GridSizeZ"), 128},
        {TEXT("r.VolumetricFog.TemporalReprojection"), P != TEXT("VFNoTR")}, {TEXT("r.VolumetricFog.Jitter"), 1},
        {TEXT("r.VolumetricFog.HistoryWeight"), .9}, {TEXT("r.AntiAliasingMethod"), 0},
        {TEXT("r.ScreenPercentage"), 100}, {TEXT("r.DynamicRes.OperationMode"), 0},
        {TEXT("r.EyeAdaptation.PreExposureOverride"), 1}, {TEXT("r.DefaultFeature.AutoExposure"), 0},
        {TEXT("r.MotionBlurQuality"), 0}, {TEXT("r.BloomQuality"), 0},
        {TEXT("r.DepthOfFieldQuality"), 0}, {TEXT("r.OneFrameThreadLag"), 0}, {TEXT("r.RDG.Events"), 3}, {TEXT("r.ProfileGPU.ShowUI"), 0}
    };
    for (const auto& S : Settings) if (!Set(S.Key, S.Value, Error)) return false;
    return true;
}

void ObserveUEFogCompareView(FRDGBuilder& GraphBuilder, const FSceneView& View, const FIntRect& Rect, FIntPoint Extent)
{
    if (!Request) return;
    FScopeLock Lock(&ViewLock);
    auto J = MakeShared<FJsonObject>();
    const uint32 Frame = View.Family->FrameNumber;
    J->SetNumberField(TEXT("engineFrame"), Frame);
    J->SetNumberField(TEXT("renderedViewIndex"), ++ViewCount);
    J->SetArrayField(TEXT("viewMin"), N({double(Rect.Min.X), double(Rect.Min.Y)}));
    J->SetArrayField(TEXT("viewSize"), N({double(Rect.Width()), double(Rect.Height())}));
    J->SetArrayField(TEXT("sceneExtent"), N({double(Extent.X), double(Extent.Y)}));
    const FVector Eye = View.ViewMatrices.GetViewOrigin(), K = View.GetViewDirection();
    J->SetArrayField(TEXT("cameraWorldCm"), N({Eye.X, Eye.Y, Eye.Z}));
    J->SetArrayField(TEXT("cameraForward"), N({K.X, K.Y, K.Z}));
    auto Matrix = [](const FMatrix& M)
    {
        TArray<TSharedPtr<FJsonValue>> A;
        for (int R = 0; R < 4; ++R) for (int C = 0; C < 4; ++C) A.Add(MakeShared<FJsonValueNumber>(M.M[R][C]));
        return A;
    };
    J->SetArrayField(TEXT("viewToClip"), Matrix(View.ViewMatrices.GetViewToClip()));
    J->SetArrayField(TEXT("worldToView"), Matrix(View.ViewMatrices.GetViewMatrix()));
    J->SetNumberField(TEXT("preExposure"), View.State ? View.State->GetPreExposure() : -1);
    J->SetStringField(TEXT("preExposureObservation"), TEXT("FSceneViewStateInterface::GetPreExposure on render thread; not an HDR GPU buffer readback"));
    J->SetNumberField(TEXT("autoExposureMethod"), int32(View.FinalPostProcessSettings.AutoExposureMethod));
    J->SetNumberField(TEXT("autoExposureBias"), View.FinalPostProcessSettings.AutoExposureBias);
    J->SetBoolField(TEXT("physicalCameraExposure"), View.FinalPostProcessSettings.AutoExposureApplyPhysicalCameraExposure);
    J->SetBoolField(TEXT("fogShowFlag"), View.Family->EngineShowFlags.Fog);
    J->SetBoolField(TEXT("volumetricFogShowFlag"), View.Family->EngineShowFlags.VolumetricFog);
    J->SetBoolField(TEXT("localExposureShowFlag"), View.Family->EngineShowFlags.LocalExposure);
    J->SetNumberField(TEXT("integrateExecutions"), 0);
    J->SetNumberField(TEXT("compositeExecutions"), 0);
    auto C = MakeShared<FJsonObject>();
    for (const TCHAR* Name : ObservedCVars)
        if (auto* V = IConsoleManager::Get().FindConsoleVariable(Name)) C->SetNumberField(Name, V->GetFloat());
    J->SetObjectField(TEXT("cvars"), C);
    FUEFogDynamicState Motion;
    const bool bDynamic = IsUEFogDynamicActive();
    if (bDynamic)
    {
        if (!GetUEFogDynamicState(Frame, Motion))
        {
            UE_LOG(LogTemp, Error, TEXT("Dynamic observation has no matching view snapshot"));
            FPlatformMisc::RequestExitWithStatus(false, 5);
            return;
        }
        auto D = MakeShared<FJsonObject>();
        D->SetNumberField(TEXT("outputIndex"), Motion.OutputIndex);
        D->SetNumberField(TEXT("timeSeconds"), Motion.TimeSeconds);
        D->SetNumberField(TEXT("phase"), Motion.Phase);
        D->SetNumberField(TEXT("gameFrame"), Motion.GameFrame);
        D->SetArrayField(TEXT("sphereWorldCm"), N({Motion.SphereWorldCm.X, Motion.SphereWorldCm.Y, Motion.SphereWorldCm.Z}));
        D->SetArrayField(TEXT("photonDirection"), N({Motion.PhotonDirection.X, Motion.PhotonDirection.Y, Motion.PhotonDirection.Z}));
        TSharedPtr<FJsonObject> Actors;
        if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Motion.ActorJson), Actors))
        {
            UE_LOG(LogTemp, Error, TEXT("Dynamic actor value snapshot could not be decoded"));
            FPlatformMisc::RequestExitWithStatus(false, 5);
            return;
        }
        D->SetObjectField(TEXT("actors"), Actors);
        TArray<TSharedPtr<FJsonValue>> FogProxies, SunProxies;
        if (const FScene* Scene = View.Family->Scene->GetRenderScene())
        {
            for (const auto* P : Scene->LocalFogVolumes)
            {
                auto X = MakeShared<FJsonObject>(); const FVector Pos = P->FogTransform.GetLocation();
                X->SetArrayField(TEXT("positionCm"), N({Pos.X, Pos.Y, Pos.Z}));
                X->SetNumberField(TEXT("radiusCm"), P->FogUniformScale);
                X->SetNumberField(TEXT("radialExtinction"), P->RadialFogExtinction);
                X->SetNumberField(TEXT("heightExtinction"), P->HeightFogExtinction);
                FogProxies.Add(MakeShared<FJsonValueObject>(X));
            }
            for (const auto* L : Scene->DirectionalLights)
            {
                const FVector SunDirection = L->Proxy->GetDirection();
                auto X = MakeShared<FJsonObject>(); X->SetArrayField(TEXT("photonDirection"), N({SunDirection.X, SunDirection.Y, SunDirection.Z}));
                SunProxies.Add(MakeShared<FJsonValueObject>(X));
            }
        }
        D->SetArrayField(TEXT("lfvRenderProxies"), FogProxies);
        D->SetArrayField(TEXT("sunRenderProxies"), SunProxies);
        J->SetObjectField(TEXT("dynamic"), D);
    }
    LastView = J;
    const bool bScreenshot = ScreenshotArmed.Exchange(false);
    if (bScreenshot) ScreenshotView = J;
    // This empty GPU marker binds the screenshot view to independent native GPU events.
    // It is not evidence that a native or custom fog dispatch ran.
    GraphBuilder.AddPass(RDG_EVENT_NAME("UEFogCompare Witness View=%llu EngineFrame=%u", ViewCount, Frame),
        ERDGPassFlags::NeverCull, [bScreenshot, bDynamic, Output = Motion.OutputIndex,
            Count = bDynamic ? Request->GetIntegerField(TEXT("dynamicFrames")) : 0](FRHICommandListImmediate& RHICmdList)
        {
            if (bDynamic && Output == 0) { SCOPED_DRAW_EVENT(RHICmdList, UEFogDynamicFirst); }
            else if (bDynamic && Output == Count / 2) { SCOPED_DRAW_EVENT(RHICmdList, UEFogDynamicMiddle); }
            else if (bDynamic && Output == Count - 1) { SCOPED_DRAW_EVENT(RHICmdList, UEFogDynamicLast); }
            else if (bDynamic && Output >= 0) { SCOPED_DRAW_EVENT(RHICmdList, UEFogDynamicFrame); }
            else if (bScreenshot) { SCOPED_DRAW_EVENT(RHICmdList, UEFogCompareScreenshotWitness); }
            else { SCOPED_DRAW_EVENT(RHICmdList, UEFogCompareWitness); }
        });
}

void ObserveUEFogDynamicShaderInputs(uint32 Frame, const FVector& SphereTranslated,
    const FVector& PreViewTranslation, const FVector& Photon, FIntPoint FogSize, uint32 Steps)
{
    FScopeLock Lock(&ViewLock);
    if (!LastView || LastView->GetNumberField(TEXT("engineFrame")) != Frame) return;
    auto D = LastView->GetObjectField(TEXT("dynamic"));
    D->SetArrayField(TEXT("shaderSphereTranslatedCm"), N({SphereTranslated.X, SphereTranslated.Y, SphereTranslated.Z}));
    D->SetArrayField(TEXT("preViewTranslationCm"), N({PreViewTranslation.X, PreViewTranslation.Y, PreViewTranslation.Z}));
    D->SetArrayField(TEXT("shaderPhotonDirection"), N({Photon.X, Photon.Y, Photon.Z}));
    D->SetArrayField(TEXT("shaderFogSize"), N({double(FogSize.X), double(FogSize.Y)}));
    D->SetNumberField(TEXT("shaderSteps"), Steps);
}

void CountUEFogComparePass(uint32 Frame, bool bComposite)
{
    FScopeLock Lock(&ViewLock);
    if (LastView && LastView->GetNumberField(TEXT("engineFrame")) == Frame)
    {
        const TCHAR* Key = bComposite ? TEXT("compositeExecutions") : TEXT("integrateExecutions");
        LastView->SetNumberField(Key, LastView->GetNumberField(Key) + 1);
    }
}
TSharedPtr<FJsonObject> GetUEFogCompareView()
{
    FScopeLock Lock(&ViewLock);
    if (!LastView) return nullptr;
    return MakeShared<FJsonObject>(*LastView);
}
void ArmUEFogCompareScreenshot() { ScreenshotArmed.Store(true); }
TSharedPtr<FJsonObject> GetUEFogCompareScreenshotView()
{
    FScopeLock Lock(&ViewLock);
    if (!ScreenshotView) return nullptr;
    return MakeShared<FJsonObject>(*ScreenshotView);
}
