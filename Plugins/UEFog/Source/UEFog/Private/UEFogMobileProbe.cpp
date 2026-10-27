#include "UEFogMobileProbe.h"
#include "GlobalShader.h"
#include "ShaderParameterStruct.h"
#include "SceneViewExtension.h"
#include "SceneView.h"
#include "SceneManagement.h"
#include "SceneTexturesConfig.h"
#include "PostProcess/PostProcessMaterialInputs.h"
#include "PostProcess/PostProcessing.h"
#include "RenderGraphUtils.h"
#include "RHIGPUReadback.h"
#include "DynamicRHI.h"
#include "RHIStrings.h"
#include "Engine/World.h"
#include "Materials/Material.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include <atomic>

// Register on the host cooker as well, but compile only the isolated mobile
// Vulkan permutations. The old SM5 shader classes and filters are unchanged.
class FMobileProbeWriteCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FMobileProbeWriteCS);
    SHADER_USE_PARAMETER_STRUCT(FMobileProbeWriteCS, FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER(float, ProbeScale)
        SHADER_PARAMETER(uint32, RunTagA)
        SHADER_PARAMETER(uint32, RunTagB)
        SHADER_PARAMETER(uint32, ProbeFrame)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float4>, KnownOutput)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P)
    { return P.Platform == SP_VULKAN_ES3_1_ANDROID; }
};
class FMobileProbeSampleCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FMobileProbeSampleCS);
    SHADER_USE_PARAMETER_STRUCT(FMobileProbeSampleCS, FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER_STRUCT_REF(FViewUniformShaderParameters, View)
        SHADER_PARAMETER_RDG_UNIFORM_BUFFER(FMobileSceneTextureUniformParameters, MobileSceneTextures)
        SHADER_PARAMETER(uint32, ProbeFrame)
        SHADER_PARAMETER(FIntPoint, ProbeViewMin)
        SHADER_PARAMETER(FIntPoint, ProbeViewSize)
        SHADER_PARAMETER(FIntPoint, ProbeSceneExtent)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float4>, KnownInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, SceneInput)
        SHADER_PARAMETER_RDG_BUFFER_UAV(RWStructuredBuffer<float4>, ProbeOutput)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P)
    { return P.Platform == SP_VULKAN_ES3_1_ANDROID; }
};
IMPLEMENT_GLOBAL_SHADER(FMobileProbeWriteCS, "/Plugin/UEFog/Private/UEFogMobileProbe.usf", "WriteKnownCS", SF_Compute);
IMPLEMENT_GLOBAL_SHADER(FMobileProbeSampleCS, "/Plugin/UEFog/Private/UEFogMobileProbe.usf", "SampleProbeCS", SF_Compute);

#if PLATFORM_ANDROID
namespace UEFogMobileProbePrivate
{
struct FProbeState
{
    FString RunId, Directory, Mode, BuildId, Source;
    float Scale = 1;
    uint32 TagA = 0, TagB = 0;
    std::atomic<bool> Ready{false};
    const FSceneInterface* Scene = nullptr;
    TSharedPtr<FJsonObject> Fixture, Record;
    TUniquePtr<FRHIGPUBufferReadback> Readback;
    uint32 MobileCallbacks = 0, BeforeCallbacks = 0, ScheduledFrame = 0;
    bool Scheduled = false, Finished = false;
};
TUniquePtr<FProbeState> Probe;
constexpr uint32 BufferBytes = 20 * sizeof(FVector4f);

TArray<TSharedPtr<FJsonValue>> Numbers(std::initializer_list<double> Values)
{
    TArray<TSharedPtr<FJsonValue>> A;
    for (double X : Values) A.Add(MakeShared<FJsonValueNumber>(X));
    return A;
}
TArray<TSharedPtr<FJsonValue>> Matrix(const FMatrix& M)
{
    TArray<TSharedPtr<FJsonValue>> A;
    for (int32 R = 0; R < 4; ++R) for (int32 C = 0; C < 4; ++C) A.Add(MakeShared<FJsonValueNumber>(M.M[R][C]));
    return A;
}
int32 CVar(const TCHAR* Name)
{
    auto* V = IConsoleManager::Get().FindConsoleVariable(Name); return V ? V->GetInt() : -1;
}
TSharedRef<FJsonObject> Desc(FRDGTextureRef T)
{
    auto J = MakeShared<FJsonObject>(); J->SetBoolField(TEXT("valid"), T != nullptr);
    if (T)
    {
        const FPixelFormatInfo& P = GPixelFormats[T->Desc.Format];
        J->SetStringField(TEXT("name"), T->Name); J->SetStringField(TEXT("format"), P.Name);
        J->SetNumberField(TEXT("platformFormat"), P.PlatformFormat);
        J->SetNumberField(TEXT("flags"), double(uint64(T->Desc.Flags)));
        J->SetBoolField(TEXT("memoryless"), EnumHasAnyFlags(T->Desc.Flags, TexCreate_Memoryless));
        J->SetNumberField(TEXT("samples"), T->Desc.NumSamples);
        J->SetArrayField(TEXT("extent"), Numbers({double(T->Desc.Extent.X), double(T->Desc.Extent.Y)}));
        J->SetNumberField(TEXT("ueCapabilities"), uint32(P.Capabilities));
        J->SetBoolField(TEXT("rtSupported"), EnumHasAllFlags(P.Capabilities, EPixelFormatCapabilities::RenderTarget));
        J->SetBoolField(TEXT("uavSupported"), EnumHasAllFlags(P.Capabilities, EPixelFormatCapabilities::UAV));
        J->SetBoolField(TEXT("sampleSupported"), EnumHasAllFlags(P.Capabilities, EPixelFormatCapabilities::TextureSample));
    }
    return J;
}
void Save()
{
    Probe->Record->SetNumberField(TEXT("mobilePrePostCallbacks"), Probe->MobileCallbacks);
    Probe->Record->SetNumberField(TEXT("beforeDofCallbacks"), Probe->BeforeCallbacks);
    FString Text; FJsonSerializer::Serialize(Probe->Record.ToSharedRef(), TJsonWriterFactory<>::Create(&Text));
    if (!FFileHelper::SaveStringToFile(Text, *FPaths::Combine(Probe->Directory, TEXT("probe.json")), FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
        UE_LOG(LogTemp, Error, TEXT("Mobile probe could not persist metadata"));
}
TSharedRef<FJsonObject> Snapshot(const FSceneView& V, FScreenPassTexture Color, TRDGUniformBufferRef<FMobileSceneTextureUniformParameters> Mobile)
{
    auto J = MakeShared<FJsonObject>();
    J->SetStringField(TEXT("kind"), TEXT("UEFogLab.AndroidPathProbe")); J->SetNumberField(TEXT("schemaVersion"), 1);
    J->SetStringField(TEXT("mode"), Probe->Mode); J->SetStringField(TEXT("runId"), Probe->RunId);
    J->SetStringField(TEXT("buildId"), Probe->BuildId); J->SetStringField(TEXT("sourceRevision"), Probe->Source);
    J->SetNumberField(TEXT("pid"), FPlatformProcess::GetCurrentProcessId());
    J->SetStringField(TEXT("rhi"), GDynamicRHI->GetName());
    J->SetStringField(TEXT("featureLevel"), V.GetFeatureLevel() == ERHIFeatureLevel::ES3_1 ? TEXT("ES3_1") : TEXT("other"));
    J->SetStringField(TEXT("shaderFormat"), LegacyShaderPlatformToShaderFormat(V.GetShaderPlatform()).ToString());
    J->SetStringField(TEXT("callback"), TEXT("Mobile BeforeDOF"));
    J->SetStringField(TEXT("depthSource"), TEXT("MobileSceneTextures.SceneDepthTexture; FORCE_DEPTH_TEXTURE_READS=1; native DeviceZ"));
    J->SetStringField(TEXT("colorStage"), TEXT("BeforeDOF, before mobile bloom and tonemap; native float linear pre-exposed SceneColor"));
    J->SetNumberField(TEXT("frame"), V.Family->FrameNumber); J->SetNumberField(TEXT("scale"), Probe->Scale);
    J->SetArrayField(TEXT("tags"), Numbers({double(Probe->TagA), double(Probe->TagB)}));
    J->SetArrayField(TEXT("viewMin"), Numbers({double(Color.ViewRect.Min.X), double(Color.ViewRect.Min.Y)}));
    J->SetArrayField(TEXT("viewSize"), Numbers({double(Color.ViewRect.Width()), double(Color.ViewRect.Height())}));
    J->SetArrayField(TEXT("surfaceSize"), Numbers({double(V.Family->RenderTarget->GetSizeXY().X), double(V.Family->RenderTarget->GetSizeXY().Y)}));
    J->SetArrayField(TEXT("worldToClip"), Matrix(V.ViewMatrices.GetWorldToClip()));
    J->SetArrayField(TEXT("clipToWorld"), Matrix(V.ViewMatrices.GetClipToWorld()));
    J->SetArrayField(TEXT("cameraCm"), Numbers({V.ViewMatrices.GetViewOrigin().X, V.ViewMatrices.GetViewOrigin().Y, V.ViewMatrices.GetViewOrigin().Z}));
    J->SetNumberField(TEXT("preExposure"), V.State ? V.State->GetPreExposure() : -1);
    J->SetStringField(TEXT("preExposureObservation"), TEXT("CPU view-state; On additionally reads actual View.PreExposure in GPU buffer"));
    J->SetNumberField(TEXT("projectionSignY"), GProjectionSignY);
    J->SetObjectField(TEXT("sceneColor"), Desc(Color.Texture));
    const auto* M = Mobile->GetContents();
    J->SetObjectField(TEXT("sceneDepth"), Desc(M->SceneDepthTexture)); J->SetObjectField(TEXT("depthAuxNotSampled"), Desc(M->SceneDepthAuxTexture));
    J->SetObjectField(TEXT("fixture"), Probe->Fixture.ToSharedRef());
    auto Values = MakeShared<FJsonObject>();
    for (const TCHAR* Name : {TEXT("r.Mobile.ShadingPath"), TEXT("r.MobileHDR"), TEXT("r.Mobile.AntiAliasing"), TEXT("r.MSAACount"),
        TEXT("r.DynamicRes.OperationMode"), TEXT("r.Mobile.ForceDepthResolve"), TEXT("r.UEFog.Enabled"), TEXT("r.Fog"),
        TEXT("r.VolumetricFog"), TEXT("r.LocalFogVolume"), TEXT("r.SupportLocalFogVolumes")}) Values->SetNumberField(Name, CVar(Name));
    J->SetObjectField(TEXT("cvars"), Values);
    J->SetNumberField(TEXT("writeDispatches"), 0); J->SetNumberField(TEXT("sampleDispatches"), 0);
    J->SetNumberField(TEXT("readbackRequests"), 0); J->SetBoolField(TEXT("gpuCompletionConfirmed"), false);
    J->SetNumberField(TEXT("oldFogPasses"), 0); // Existing old extension is SM5-only and all fog CVars are zero.
    return J;
}
class FProbeExtension final : public FSceneViewExtensionBase
{
public:
    explicit FProbeExtension(const FAutoRegister& R) : FSceneViewExtensionBase(R) {}
    bool Matches(const FSceneView& V) const
    { return Probe && Probe->Ready.load(std::memory_order_acquire) && V.bIsGameView && V.Family->Scene == Probe->Scene && V.Family->Views.Num() == 1; }
    virtual void PrePostProcessPassMobile_RenderThread(FRDGBuilder&, const FSceneView& V, const FMobilePostProcessingInputs&) override
    { if (Matches(V)) ++Probe->MobileCallbacks; }
    virtual void SubscribeToPostProcessingPass(EPostProcessingPass Pass, const FSceneView& V, FPostProcessingPassDelegateArray& Callbacks, bool) override
    {
        if (Pass == EPostProcessingPass::BeforeDOF && Matches(V) && V.GetFeatureLevel() == ERHIFeatureLevel::ES3_1)
            Callbacks.Add(FPostProcessingPassDelegate::CreateRaw(this, &FProbeExtension::Render));
    }
    FScreenPassTexture Render(FRDGBuilder& Graph, const FSceneView& V, const FPostProcessMaterialInputs& Inputs)
    {
        ++Probe->BeforeCallbacks;
        const auto Mobile = Inputs.SceneTextures.MobileSceneTextures.GetUniformBuffer();
        const auto Color = FScreenPassTexture::CopyFromSlice(Graph, Inputs.GetInput(EPostProcessMaterialInput::SceneColor));
        if (!Mobile || !Color.IsValid()) return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        if (Probe->Readback && !Probe->Finished && Probe->Readback->IsReady())
        {
            TArray<uint8> Bytes; Bytes.SetNumUninitialized(BufferBytes);
            if (const void* Data = Probe->Readback->Lock(BufferBytes))
            {
                FMemory::Memcpy(Bytes.GetData(), Data, BufferBytes); Probe->Readback->Unlock();
                const bool OK = FFileHelper::SaveArrayToFile(Bytes, *FPaths::Combine(Probe->Directory, TEXT("probe-buffer.bin")));
                Probe->Record->SetBoolField(TEXT("gpuCompletionConfirmed"), OK);
                Probe->Record->SetStringField(TEXT("status"), OK ? TEXT("complete") : TEXT("failed"));
                Probe->Record->SetNumberField(TEXT("completionFrame"), V.Family->FrameNumber);
                Probe->Record->SetNumberField(TEXT("bufferBytes"), BufferBytes);
                Probe->Record->SetNumberField(TEXT("bufferStride"), sizeof(FVector4f));
                Probe->Record->SetStringField(TEXT("rowPitch"), TEXT("not applicable: tightly packed structured buffer, 20 float4 records"));
                Probe->Record->SetStringField(TEXT("dtype"), TEXT("<f4"));
                Probe->Finished = true; Save();
            }
        }
        if (Probe->BeforeCallbacks >= 180 && Probe->Mode == TEXT("Off") && Probe->BeforeCallbacks % 120 == 60)
        {
            Probe->Record = Snapshot(V, Color, Mobile); Probe->Record->SetStringField(TEXT("status"), TEXT("control-complete")); Save();
        }
        if (Probe->BeforeCallbacks == 180 && Probe->Mode == TEXT("On") && !Probe->Scheduled)
        {
            Probe->Scheduled = true; Probe->ScheduledFrame = V.Family->FrameNumber;
            Probe->Record = Snapshot(V, Color, Mobile);
            const auto Cap = GPixelFormats[PF_FloatRGBA].Capabilities;
            if (!EnumHasAllFlags(Cap, EPixelFormatCapabilities::UAV | EPixelFormatCapabilities::TextureLoad))
            { Probe->Record->SetStringField(TEXT("status"), TEXT("unsupported")); Save(); return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph); }
            auto* Known = Graph.CreateTexture(FRDGTextureDesc::Create2D(FIntPoint(2, 1), PF_FloatRGBA, FClearValueBinding::None,
                TexCreate_ShaderResource | TexCreate_UAV), TEXT("UEFog.MobileProbe.KnownRGBA16F"));
            Probe->Record->SetObjectField(TEXT("knownTexture"), Desc(Known));
            auto* W = Graph.AllocParameters<FMobileProbeWriteCS::FParameters>();
            W->ProbeScale = Probe->Scale; W->RunTagA = Probe->TagA; W->RunTagB = Probe->TagB;
            W->ProbeFrame = Probe->ScheduledFrame; W->KnownOutput = Graph.CreateUAV(Known);
            TShaderMapRef<FMobileProbeWriteCS> Write(GetGlobalShaderMap(V.GetFeatureLevel()));
            FComputeShaderUtils::AddPass(Graph, RDG_EVENT_NAME("UEFog.MobileProbe.Write"), Write, W, FIntVector(1, 1, 1));
            auto* Buffer = Graph.CreateBuffer(FRDGBufferDesc::CreateStructuredDesc(sizeof(FVector4f), 20), TEXT("UEFog.MobileProbe.Output"));
            auto* S = Graph.AllocParameters<FMobileProbeSampleCS::FParameters>();
            S->View = V.ViewUniformBuffer; S->MobileSceneTextures = Mobile; S->ProbeFrame = Probe->ScheduledFrame;
            S->ProbeViewMin = Color.ViewRect.Min; S->ProbeViewSize = Color.ViewRect.Size(); S->ProbeSceneExtent = Color.Texture->Desc.Extent;
            S->KnownInput = Known; S->SceneInput = Color.Texture; S->ProbeOutput = Graph.CreateUAV(Buffer);
            TShaderMapRef<FMobileProbeSampleCS> Sample(GetGlobalShaderMap(V.GetFeatureLevel()));
            FComputeShaderUtils::AddPass(Graph, RDG_EVENT_NAME("UEFog.MobileProbe.Sample"), Sample, S, FIntVector(1, 1, 1));
            Probe->Readback = MakeUnique<FRHIGPUBufferReadback>(TEXT("UEFog.MobileProbe.Copy"));
            AddEnqueueCopyPass(Graph, Probe->Readback.Get(), Buffer, BufferBytes);
            Probe->Record->SetStringField(TEXT("status"), TEXT("pending"));
            Probe->Record->SetNumberField(TEXT("writeDispatches"), 1); Probe->Record->SetNumberField(TEXT("sampleDispatches"), 1);
            Probe->Record->SetNumberField(TEXT("readbackRequests"), 1); Save();
        }
        // This diagnostic samples separate resources and never modifies SceneColor.
        // The helper also copies to OverrideOutput when this is a terminal callback.
        return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
    }
};
}
#endif

bool ApplyUEFogMobileProbeLaunch(FString& Error)
{
    FString Mode;
    if (!FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileProbe="), Mode)) return true;
#if PLATFORM_ANDROID
    using namespace UEFogMobileProbePrivate;
    Probe = MakeUnique<FProbeState>(); Probe->Mode = Mode;
    if ((Mode != TEXT("Off") && Mode != TEXT("On")) ||
        !FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileRun="), Probe->RunId) || Probe->RunId.Len() != 32 ||
        !FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileOutput="), Probe->Directory))
    { Error = TEXT("Probe requires On/Off plus an active private startup witness"); Probe.Reset(); return false; }
    FParse::Value(FCommandLine::Get(), TEXT("FogLabBaselineBuild="), Probe->BuildId);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabBaselineSource="), Probe->Source);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabProbeScale="), Probe->Scale);
    if (Probe->Scale != 1 && Probe->Scale != 2) { Error = TEXT("Only frozen probe scales 1 and 2 are allowed"); Probe.Reset(); return false; }
    const uint32 Tags = FCString::Strtoui64(*Probe->RunId.Left(5), nullptr, 16);
    Probe->TagA = Tags & 1023; Probe->TagB = (Tags >> 10) & 1023;
    IConsoleManager::Get().FindConsoleVariable(TEXT("r.Mobile.ForceDepthResolve"))->Set(1, ECVF_SetByCommandline);
    return true;
#else
    Error = TEXT("Mobile resource probe is Android-only"); return false;
#endif
}
TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> CreateUEFogMobileProbeExtension()
{
#if PLATFORM_ANDROID
    using namespace UEFogMobileProbePrivate;
    if (Probe) return FSceneViewExtensions::NewExtension<FProbeExtension>();
#endif
    return nullptr;
}
void UEFogMobileProbeFixtureReady(UWorld* World, TSharedRef<FJsonObject> Fixture)
{
#if PLATFORM_ANDROID
    using namespace UEFogMobileProbePrivate;
    if (Probe) { Probe->Scene = World->Scene; Probe->Fixture = Fixture; Probe->Ready.store(true, std::memory_order_release); }
#endif
}

void UEFogMobileProbePrepareCookContent()
{
#if WITH_EDITOR
    // An explicit startup package for this target cooker; no engine asset is
    // edited/copied into the source repo. The final pak gate must find it.
    if (IsRunningCookCommandlet() && FString(FCommandLine::Get()).Contains(TEXT("TargetPlatform=Android")))
    {
        UMaterial* Material = LoadObject<UMaterial>(nullptr, TEXT("/Engine/EngineDebugMaterials/LevelColorationUnlitMaterial.LevelColorationUnlitMaterial"));
        check(Material); Material->AddToRoot();
        UE_LOG(LogTemp, Display, TEXT("UEFog mobile probe explicit cook content: %s blend=%d shading=%d"),
            *Material->GetPathName(), int32(Material->GetBlendMode()), Material->GetShadingModels().GetShadingModelField());
    }
#endif
}
