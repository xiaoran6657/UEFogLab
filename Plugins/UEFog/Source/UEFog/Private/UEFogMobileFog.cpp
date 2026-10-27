#include "UEFogMobileFog.h"
#include "UEFogCompare.h"
#include "GlobalShader.h"
#include "ShaderParameterStruct.h"
#include "SceneViewExtension.h"
#include "SceneView.h"
#include "SceneManagement.h"
#include "SceneTexturesConfig.h"
#include "PostProcess/PostProcessMaterialInputs.h"
#include "PostProcess/PostProcessing.h"
#include "ScreenPass.h"
#include "RenderGraphUtils.h"
#include "RHIGPUReadback.h"
#include "DynamicRHI.h"
#include "RHIStrings.h"
#include "Engine/World.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include <atomic>

// Shader types are registered in the host cooker; only the target mobile
// Vulkan permutations compile. No changes to any old SM5 shader class.
class FMobileUniformIntegrateCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FMobileUniformIntegrateCS);
    SHADER_USE_PARAMETER_STRUCT(FMobileUniformIntegrateCS, FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER_STRUCT_REF(FViewUniformShaderParameters, View)
        SHADER_PARAMETER_RDG_UNIFORM_BUFFER(FMobileSceneTextureUniformParameters, MobileSceneTextures)
        SHADER_PARAMETER(FMatrix44f, ClipToTranslatedWorld)
        SHADER_PARAMETER(FVector3f, CameraTranslated)
        SHADER_PARAMETER(FVector3f, SphereTranslated)
        SHADER_PARAMETER(float, ExtinctionPerMetre)
        SHADER_PARAMETER(float, FogProjectionSignY)
        SHADER_PARAMETER(FIntPoint, FogViewMin)
        SHADER_PARAMETER(FIntPoint, FogViewSize)
        SHADER_PARAMETER(FIntPoint, SceneExtent)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float4>, STOutput)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P)
    { return P.Platform == SP_VULKAN_ES3_1_ANDROID; }
};
class FMobileUniformCompositePS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FMobileUniformCompositePS);
    SHADER_USE_PARAMETER_STRUCT(FMobileUniformCompositePS, FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER_STRUCT_REF(FViewUniformShaderParameters, View)
        SHADER_PARAMETER(FIntPoint, FogViewMin)
        SHADER_PARAMETER(FIntPoint, SceneExtent)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float4>, STInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, CinInput)
        RENDER_TARGET_BINDING_SLOTS()
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P)
    { return P.Platform == SP_VULKAN_ES3_1_ANDROID; }
};
class FMobileUniformObserveCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FMobileUniformObserveCS);
    SHADER_USE_PARAMETER_STRUCT(FMobileUniformObserveCS, FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER_STRUCT_REF(FViewUniformShaderParameters, View)
        SHADER_PARAMETER_RDG_UNIFORM_BUFFER(FMobileSceneTextureUniformParameters, MobileSceneTextures)
        SHADER_PARAMETER(float, ExtinctionPerMetre)
        SHADER_PARAMETER(float, FogNearCm)
        SHADER_PARAMETER(uint32, RunTagA)
        SHADER_PARAMETER(uint32, RunTagB)
        SHADER_PARAMETER(uint32, FogFrame)
        SHADER_PARAMETER(FIntPoint, FogViewMin)
        SHADER_PARAMETER(FIntPoint, FogViewSize)
        SHADER_PARAMETER(FIntPoint, SceneExtent)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float4>, STInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, CinInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, ConsumedInput)
        SHADER_PARAMETER_RDG_BUFFER_UAV(RWStructuredBuffer<float4>, FogSamples)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P)
    { return P.Platform == SP_VULKAN_ES3_1_ANDROID; }
};
IMPLEMENT_GLOBAL_SHADER(FMobileUniformIntegrateCS, "/Plugin/UEFog/Private/UEFogMobileFog.usf", "IntegrateUniformCS", SF_Compute);
IMPLEMENT_GLOBAL_SHADER(FMobileUniformCompositePS, "/Plugin/UEFog/Private/UEFogMobileFog.usf", "CompositeUniformPS", SF_Pixel);
IMPLEMENT_GLOBAL_SHADER(FMobileUniformObserveCS, "/Plugin/UEFog/Private/UEFogMobileFog.usf", "ObserveReturnedCS", SF_Compute);

#if PLATFORM_ANDROID
namespace UEFogMobileFogPrivate
{
constexpr uint32 BufferBytes = 102 * sizeof(FVector4f);
constexpr const TCHAR* ModelKey = TEXT("MobileUniformPrescribedFull64-v1");
struct FFrameInputs
{
    uint32 Number = 0;
    FScreenPassTexture Cin, ST, Returned;
};
struct FFogState
{
    FString Mode, RunId, Directory, BuildId, Source;
    int32 Near = 400;
    double Sigma = .35; // canonical decimal option; the shader header observes float32 rounding separately.
    bool Diagnostics = false, Scheduled = false, Finished = false;
    uint32 TagA = 0, TagB = 0, MobileCallbacks = 0, ProducerCallbacks = 0, ObserverCallbacks = 0;
    std::atomic<bool> Ready{false};
    const FSceneInterface* Scene = nullptr;
    TSharedPtr<FJsonObject> Fixture, Record;
    FFrameInputs Current; // Only used by adjacent delegates in the SAME RDG graph/frame.
    TUniquePtr<FRHIGPUBufferReadback> Readback;
};
TUniquePtr<FFogState> Fog;

TArray<TSharedPtr<FJsonValue>> Numbers(std::initializer_list<double> V)
{
    TArray<TSharedPtr<FJsonValue>> A;
    for (double X : V) A.Add(MakeShared<FJsonValueNumber>(X));
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
    const auto* Value = IConsoleManager::Get().FindConsoleVariable(Name);
    return Value ? Value->GetInt() : -1;
}
TSharedRef<FJsonObject> Desc(FRDGTextureRef Texture)
{
    auto J = MakeShared<FJsonObject>(); J->SetBoolField(TEXT("valid"), Texture != nullptr);
    if (Texture)
    {
        const auto& P = GPixelFormats[Texture->Desc.Format];
        J->SetStringField(TEXT("name"), Texture->Name); J->SetStringField(TEXT("format"), P.Name);
        J->SetNumberField(TEXT("platformFormat"), P.PlatformFormat);
        J->SetNumberField(TEXT("flags"), double(uint64(Texture->Desc.Flags)));
        J->SetNumberField(TEXT("samples"), Texture->Desc.NumSamples);
        J->SetArrayField(TEXT("extent"), Numbers({double(Texture->Desc.Extent.X), double(Texture->Desc.Extent.Y)}));
        J->SetBoolField(TEXT("memoryless"), EnumHasAnyFlags(Texture->Desc.Flags, TexCreate_Memoryless));
        J->SetBoolField(TEXT("rtSupported"), EnumHasAllFlags(P.Capabilities, EPixelFormatCapabilities::RenderTarget));
        J->SetBoolField(TEXT("uavSupported"), EnumHasAllFlags(P.Capabilities, EPixelFormatCapabilities::UAV));
        J->SetBoolField(TEXT("sampleSupported"), EnumHasAllFlags(P.Capabilities, EPixelFormatCapabilities::TextureSample));
    }
    return J;
}
void Save()
{
    if (!Fog->Record || Fog->Directory.IsEmpty()) return;
    Fog->Record->SetNumberField(TEXT("mobilePrePostCallbacks"), Fog->MobileCallbacks);
    Fog->Record->SetNumberField(TEXT("producerCallbacks"), Fog->ProducerCallbacks);
    Fog->Record->SetNumberField(TEXT("observerCallbacks"), Fog->ObserverCallbacks);
    FString Text;
    FJsonSerializer::Serialize(Fog->Record.ToSharedRef(), TJsonWriterFactory<>::Create(&Text));
    if (!FFileHelper::SaveStringToFile(Text, *FPaths::Combine(Fog->Directory, TEXT("fog.json")), FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
        UE_LOG(LogTemp, Error, TEXT("Mobile fog could not persist metadata"));
}
TSharedRef<FJsonObject> Snapshot(const FSceneView& V, FScreenPassTexture Color, TRDGUniformBufferRef<FMobileSceneTextureUniformParameters> Mobile)
{
    auto J = MakeShared<FJsonObject>();
    J->SetStringField(TEXT("kind"), TEXT("UEFogLab.MobileUniformFog")); J->SetNumberField(TEXT("schemaVersion"), 1);
    J->SetStringField(TEXT("modelKey"), ModelKey); J->SetStringField(TEXT("mode"), Fog->Mode);
    J->SetStringField(TEXT("runId"), Fog->RunId); J->SetNumberField(TEXT("pid"), FPlatformProcess::GetCurrentProcessId());
    J->SetStringField(TEXT("buildId"), Fog->BuildId); J->SetStringField(TEXT("sourceRevision"), Fog->Source);
    J->SetStringField(TEXT("rhi"), GDynamicRHI->GetName());
    J->SetStringField(TEXT("featureLevel"), V.GetFeatureLevel() == ERHIFeatureLevel::ES3_1 ? TEXT("ES3_1") : TEXT("other"));
    J->SetStringField(TEXT("shaderFormat"), LegacyShaderPlatformToShaderFormat(V.GetShaderPlatform()).ToString());
    J->SetStringField(TEXT("producerCallback"), TEXT("Mobile BeforeDOF.Producer"));
    J->SetStringField(TEXT("observerCallback"), TEXT("Mobile BeforeDOF.Observer"));
    J->SetStringField(TEXT("colorStage"), TEXT("native linear pre-exposed input -> RGBA16F RT returned -> engine delegate -> actual observer SceneColor; before bloom/tonemap"));
    J->SetStringField(TEXT("depthSource"), TEXT("MobileSceneTextures.SceneDepthTexture; FORCE_DEPTH_TEXTURE_READS=1; native DeviceZ"));
    J->SetStringField(TEXT("pixelSelection"), TEXT("integer-percent: (PercentInt * ViewSize) // 100"));
    J->SetNumberField(TEXT("frame"), V.Family->FrameNumber);
    J->SetNumberField(TEXT("nearCm"), Fog->Near); J->SetNumberField(TEXT("extinctionPerMetre"), Fog->Sigma);
    J->SetNumberField(TEXT("steps"), 64); J->SetNumberField(TEXT("earlyOut"), 0);
    J->SetNumberField(TEXT("preExposure"), V.State ? V.State->GetPreExposure() : -1);
    J->SetArrayField(TEXT("sourceJ"), Numbers({.35, .60, .90}));
    J->SetArrayField(TEXT("sphereCentreCm"), Numbers({800, 0, 0})); J->SetNumberField(TEXT("sphereRadiusCm"), 400);
    J->SetArrayField(TEXT("cameraCm"), Numbers({V.ViewMatrices.GetViewOrigin().X, V.ViewMatrices.GetViewOrigin().Y, V.ViewMatrices.GetViewOrigin().Z}));
    J->SetArrayField(TEXT("viewMin"), Numbers({double(Color.ViewRect.Min.X), double(Color.ViewRect.Min.Y)}));
    J->SetArrayField(TEXT("viewSize"), Numbers({double(Color.ViewRect.Width()), double(Color.ViewRect.Height())}));
    J->SetArrayField(TEXT("surfaceSize"), Numbers({double(V.Family->RenderTarget->GetSizeXY().X), double(V.Family->RenderTarget->GetSizeXY().Y)}));
    J->SetArrayField(TEXT("worldToClip"), Matrix(V.ViewMatrices.GetWorldToClip()));
    J->SetArrayField(TEXT("clipToWorld"), Matrix(V.ViewMatrices.GetClipToWorld()));
    J->SetNumberField(TEXT("projectionSignY"), GProjectionSignY);
    J->SetObjectField(TEXT("sceneColorInput"), Desc(Color.Texture));
    J->SetObjectField(TEXT("sceneDepth"), Desc(Mobile->GetContents()->SceneDepthTexture));
    J->SetObjectField(TEXT("fixture"), Fog->Fixture.ToSharedRef());
    J->SetNumberField(TEXT("integrateDispatches"), 0); J->SetNumberField(TEXT("compositePasses"), 0);
    J->SetNumberField(TEXT("sampleDispatches"), 0); J->SetNumberField(TEXT("readbackRequests"), 0);
    J->SetBoolField(TEXT("gpuCompletionConfirmed"), false);
    J->SetNumberField(TEXT("oldFogPasses"), 0); // structural exclusion, not a profiler measurement
    J->SetBoolField(TEXT("overrideOutputActive"), false);
    auto C = MakeShared<FJsonObject>();
    for (const TCHAR* Name : {TEXT("r.Mobile.ShadingPath"), TEXT("r.MobileHDR"), TEXT("r.Mobile.AntiAliasing"), TEXT("r.MSAACount"),
        TEXT("r.DynamicRes.OperationMode"), TEXT("r.Mobile.ForceDepthResolve"), TEXT("r.UEFog.Enabled"), TEXT("r.Fog"),
        TEXT("r.VolumetricFog"), TEXT("r.LocalFogVolume"), TEXT("r.SupportLocalFogVolumes")}) C->SetNumberField(Name, CVar(Name));
    J->SetObjectField(TEXT("cvars"), C);
    return J;
}
class FFogExtension final : public FSceneViewExtensionBase
{
public:
    explicit FFogExtension(const FAutoRegister& Auto) : FSceneViewExtensionBase(Auto) {}
    bool Matches(const FSceneView& V) const
    {
        return Fog && Fog->Ready.load(std::memory_order_acquire) && V.bIsGameView && V.Family->Scene == Fog->Scene &&
            V.Family->Views.Num() == 1 && V.GetFeatureLevel() == ERHIFeatureLevel::ES3_1 && V.GetShaderPlatform() == SP_VULKAN_ES3_1_ANDROID;
    }
    virtual void PrePostProcessPassMobile_RenderThread(FRDGBuilder&, const FSceneView& V, const FMobilePostProcessingInputs&) override
    { if (Matches(V)) ++Fog->MobileCallbacks; }
    virtual void SubscribeToPostProcessingPass(EPostProcessingPass Pass, const FSceneView& V, FPostProcessingPassDelegateArray& Callbacks, bool) override
    {
        if (Pass == EPostProcessingPass::BeforeDOF && Matches(V))
        {
            Callbacks.Add(FPostProcessingPassDelegate::CreateRaw(this, &FFogExtension::Produce));
            Callbacks.Add(FPostProcessingPassDelegate::CreateRaw(this, &FFogExtension::Observe));
        }
    }
    FScreenPassTexture Produce(FRDGBuilder& Graph, const FSceneView& V, const FPostProcessMaterialInputs& Inputs)
    {
        ++Fog->ProducerCallbacks;
        const auto Mobile = Inputs.SceneTextures.MobileSceneTextures.GetUniformBuffer();
        const auto Color = FScreenPassTexture::CopyFromSlice(Graph, Inputs.GetInput(EPostProcessMaterialInput::SceneColor));
        Fog->Current = {}; Fog->Current.Number = V.Family->FrameNumber; Fog->Current.Cin = Color;
        if (!Mobile || !Color.IsValid()) return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        if (Fog->Mode == TEXT("Off")) return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        const auto Caps = GPixelFormats[PF_FloatRGBA].Capabilities;
        if (!EnumHasAllFlags(Caps, EPixelFormatCapabilities::UAV | EPixelFormatCapabilities::TextureLoad | EPixelFormatCapabilities::RenderTarget))
        {
            Fog->Record = Snapshot(V, Color, Mobile); Fog->Record->SetStringField(TEXT("status"), TEXT("unsupported")); Save();
            return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        }
        const FIntPoint Size = Color.ViewRect.Size();
        auto* ST = Graph.CreateTexture(FRDGTextureDesc::Create2D(Size, PF_FloatRGBA, FClearValueBinding::None,
            TexCreate_ShaderResource | TexCreate_UAV), TEXT("UEFog.MobileFog.ST"));
        auto* I = Graph.AllocParameters<FMobileUniformIntegrateCS::FParameters>();
        const FVector Translation = V.ViewMatrices.GetPreViewTranslation();
        I->View = V.ViewUniformBuffer; I->MobileSceneTextures = Mobile;
        I->ClipToTranslatedWorld = FMatrix44f(V.ViewMatrices.GetClipToTranslatedWorld());
        I->CameraTranslated = FVector3f(V.ViewMatrices.GetViewOrigin() + Translation);
        I->SphereTranslated = FVector3f(FVector(800, 0, 0) + Translation);
        I->ExtinctionPerMetre = float(Fog->Sigma); I->FogProjectionSignY = GProjectionSignY;
        I->FogViewMin = Color.ViewRect.Min; I->FogViewSize = Size; I->SceneExtent = Color.Texture->Desc.Extent;
        I->STOutput = Graph.CreateUAV(ST);
        TShaderMapRef<FMobileUniformIntegrateCS> Integrate(GetGlobalShaderMap(V.GetFeatureLevel()));
        FComputeShaderUtils::AddPass(Graph, RDG_EVENT_NAME("UEFog.MobileFog.IntegrateFull64"), Integrate, I, FComputeShaderUtils::GetGroupCount(Size, FIntPoint(8, 8)));
        FScreenPassRenderTarget Output = Inputs.OverrideOutput;
        if (!Output.IsValid())
        {
            auto* Target = Graph.CreateTexture(FRDGTextureDesc::Create2D(Color.Texture->Desc.Extent, PF_FloatRGBA, FClearValueBinding::None,
                TexCreate_ShaderResource | TexCreate_RenderTargetable), TEXT("UEFog.MobileFog.ReturnedHDR"));
            Output = FScreenPassRenderTarget(Target, Color.ViewRect, ERenderTargetLoadAction::ENoAction);
        }
        auto* P = Graph.AllocParameters<FMobileUniformCompositePS::FParameters>();
        P->View = V.ViewUniformBuffer; P->FogViewMin = Color.ViewRect.Min; P->SceneExtent = Color.Texture->Desc.Extent;
        P->STInput = ST; P->CinInput = Color.Texture; P->RenderTargets[0] = Output.GetRenderTargetBinding();
        TShaderMapRef<FMobileUniformCompositePS> Composite(GetGlobalShaderMap(V.GetFeatureLevel()));
        AddDrawScreenPass(Graph, RDG_EVENT_NAME("UEFog.MobileFog.ComposeReturnedHDR"), V,
            FScreenPassTextureViewport(Output), FScreenPassTextureViewport(Color), Composite, P);
        Fog->Current.ST = FScreenPassTexture(ST, FIntRect(FIntPoint::ZeroValue, Size));
        Fog->Current.Returned = Output;
        if (Fog->Diagnostics && Fog->ProducerCallbacks == 180 && !Fog->Scheduled)
        {
            Fog->Record = Snapshot(V, Color, Mobile);
            Fog->Record->SetStringField(TEXT("status"), TEXT("pending"));
            Fog->Record->SetObjectField(TEXT("stTexture"), Desc(ST));
            Fog->Record->SetObjectField(TEXT("returnedOutput"), Desc(Output.Texture));
            Fog->Record->SetBoolField(TEXT("overrideOutputActive"), Inputs.OverrideOutput.IsValid());
            Fog->Record->SetNumberField(TEXT("integrateDispatches"), 1);
            Fog->Record->SetNumberField(TEXT("compositePasses"), 1);
            Save();
        }
        return Output; // The engine passes THIS resource to the following delegate.
    }
    FScreenPassTexture Observe(FRDGBuilder& Graph, const FSceneView& V, const FPostProcessMaterialInputs& Inputs)
    {
        ++Fog->ObserverCallbacks;
        const auto Mobile = Inputs.SceneTextures.MobileSceneTextures.GetUniformBuffer();
        const auto Actual = FScreenPassTexture::CopyFromSlice(Graph, Inputs.GetInput(EPostProcessMaterialInput::SceneColor));
        if (!Mobile || !Actual.IsValid()) return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        if (Fog->Readback && !Fog->Finished && Fog->Readback->IsReady())
        {
            TArray<uint8> Bytes; Bytes.SetNumUninitialized(BufferBytes);
            if (const void* Data = Fog->Readback->Lock(BufferBytes))
            {
                FMemory::Memcpy(Bytes.GetData(), Data, BufferBytes); Fog->Readback->Unlock();
                const bool OK = FFileHelper::SaveArrayToFile(Bytes, *FPaths::Combine(Fog->Directory, TEXT("fog-buffer.bin")));
                Fog->Record->SetBoolField(TEXT("gpuCompletionConfirmed"), OK);
                Fog->Record->SetStringField(TEXT("status"), OK ? TEXT("complete") : TEXT("failed"));
                Fog->Record->SetNumberField(TEXT("completionFrame"), V.Family->FrameNumber);
                Fog->Record->SetNumberField(TEXT("bufferBytes"), BufferBytes);
                Fog->Record->SetNumberField(TEXT("bufferStride"), sizeof(FVector4f));
                Fog->Record->SetStringField(TEXT("dtype"), TEXT("<f4"));
                Fog->Record->SetStringField(TEXT("rowPitch"), TEXT("not applicable; tightly packed structured buffer"));
                Fog->Finished = true; Fog->Readback.Reset(); Save();
            }
        }
        if (Fog->Mode == TEXT("Off") && Fog->ObserverCallbacks >= 180 && Fog->ObserverCallbacks % 120 == 60)
        {
            Fog->Record = Snapshot(V, Actual, Mobile);
            Fog->Record->SetStringField(TEXT("status"), TEXT("control-complete")); Save();
        }
        if (Fog->Mode == TEXT("Full") && Fog->Diagnostics && Fog->ProducerCallbacks == 180 && !Fog->Scheduled && Fog->Current.ST.IsValid())
        {
            // Compare the actual ENGINE input, not a separately selected sample target.
            const bool Same = Fog->Current.Number == V.Family->FrameNumber && Fog->Current.Returned == Actual;
            Fog->Record->SetBoolField(TEXT("returnedResourceEqualsObserverInput"), Same);
            Fog->Record->SetObjectField(TEXT("observerInput"), Desc(Actual.Texture));
            Fog->Record->SetNumberField(TEXT("observerFrame"), V.Family->FrameNumber);
            if (!Same)
            {
                Fog->Record->SetStringField(TEXT("status"), TEXT("failed-consumer-resource")); Save();
                return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
            }
            auto* Buffer = Graph.CreateBuffer(FRDGBufferDesc::CreateStructuredDesc(sizeof(FVector4f), 102), TEXT("UEFog.MobileFog.ObserverSamples"));
            auto* O = Graph.AllocParameters<FMobileUniformObserveCS::FParameters>();
            O->View = V.ViewUniformBuffer; O->MobileSceneTextures = Mobile;
            O->ExtinctionPerMetre = float(Fog->Sigma); O->FogNearCm = float(Fog->Near);
            O->RunTagA = Fog->TagA; O->RunTagB = Fog->TagB; O->FogFrame = V.Family->FrameNumber;
            O->FogViewMin = Actual.ViewRect.Min; O->FogViewSize = Actual.ViewRect.Size();
            O->SceneExtent = Actual.Texture->Desc.Extent;
            O->STInput = Fog->Current.ST.Texture; O->CinInput = Fog->Current.Cin.Texture;
            O->ConsumedInput = Actual.Texture;
            O->FogSamples = Graph.CreateUAV(Buffer);
            TShaderMapRef<FMobileUniformObserveCS> ObserveShader(GetGlobalShaderMap(V.GetFeatureLevel()));
            FComputeShaderUtils::AddPass(Graph, RDG_EVENT_NAME("UEFog.MobileFog.ObserveEngineConsumer"), ObserveShader, O, FIntVector(1, 1, 1));
            Fog->Readback = MakeUnique<FRHIGPUBufferReadback>(TEXT("UEFog.MobileFog.Copy"));
            AddEnqueueCopyPass(Graph, Fog->Readback.Get(), Buffer, BufferBytes);
            Fog->Scheduled = true;
            Fog->Record->SetNumberField(TEXT("sampleDispatches"), 1);
            Fog->Record->SetNumberField(TEXT("readbackRequests"), 1); Save();
        }
        // This actual chain input, not the old Cin, continues to bloom/tonemap.
        return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
    }
};
}
#endif

bool ApplyUEFogMobileFogLaunch(FString& Error)
{
    FString Mode;
    if (!FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileFog="), Mode)) return true;
#if PLATFORM_ANDROID
    using namespace UEFogMobileFogPrivate;
    FString OldProbe;
    if (FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileProbe="), OldProbe) || IsUEFogCompareActive() || (Mode != TEXT("Off") && Mode != TEXT("Full")))
    { Error = TEXT("Mobile fog requires Off/Full, with no PRE probe or Compare mode"); return false; }
    Fog = MakeUnique<FFogState>(); Fog->Mode = Mode;
    FParse::Value(FCommandLine::Get(), TEXT("FogLabFogNear="), Fog->Near);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabFogSigma="), Fog->Sigma);
    int32 Diagnostic = 0;
    FParse::Value(FCommandLine::Get(), TEXT("FogLabFogReadback="), Diagnostic);
    if ((Fog->Near != 400 && Fog->Near != 600) || (Fog->Sigma != 0 && Fog->Sigma != .35 && Fog->Sigma != .70) || (Diagnostic != 0 && Diagnostic != 1))
    { Error = TEXT("Unfrozen mobile fog parameter"); Fog.Reset(); return false; }
    Fog->Diagnostics = Diagnostic == 1 && Mode == TEXT("Full");
    FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileRun="), Fog->RunId);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabMobileOutput="), Fog->Directory);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabBaselineBuild="), Fog->BuildId);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabBaselineSource="), Fog->Source);
    if (Fog->Diagnostics && (Fog->RunId.Len() != 32 || Fog->Directory.IsEmpty() || Fog->BuildId.IsEmpty() || Fog->Source.IsEmpty()))
    { Error = TEXT("Diagnostic Full requires a private UUID/source startup witness"); Fog.Reset(); return false; }
    const uint32 Tag = FCString::Strtoui64(*Fog->RunId.Left(5), nullptr, 16);
    Fog->TagA = Tag & 1023; Fog->TagB = (Tag >> 10) & 1023;
    IConsoleManager::Get().FindConsoleVariable(TEXT("r.Mobile.ForceDepthResolve"))->Set(1, ECVF_SetByCommandline);
    return true;
#else
    Error = TEXT("Mobile Uniform fog is Android-only"); return false;
#endif
}
TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> CreateUEFogMobileFogExtension()
{
#if PLATFORM_ANDROID
    if (UEFogMobileFogPrivate::Fog) return FSceneViewExtensions::NewExtension<UEFogMobileFogPrivate::FFogExtension>();
#endif
    return nullptr;
}
void UEFogMobileFogFixtureReady(UWorld* World, TSharedRef<FJsonObject> Fixture)
{
#if PLATFORM_ANDROID
    using namespace UEFogMobileFogPrivate;
    if (Fog) { Fog->Scene = World->Scene; Fog->Fixture = Fixture; Fog->Ready.store(true, std::memory_order_release); }
#endif
}
