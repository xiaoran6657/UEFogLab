#include "UEFogViewExtension.h"
#include "UEFogLighting.h"
#include "UEFogCompare.h"
#include "GlobalShader.h"
#include "HAL/IConsoleManager.h"
#include "PostProcess/PostProcessMaterialInputs.h"
#include "RenderGraphBuilder.h"
#include "RenderGraphUtils.h"
#include "SceneView.h"
#include "ScreenPass.h"
#include "ShaderParameterStruct.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "DynamicRHI.h"

static TArray<TSharedPtr<FJsonValue>> Numbers(std::initializer_list<double> Values)
{
    TArray<TSharedPtr<FJsonValue>> Result;
    for (double V : Values) Result.Add(MakeShared<FJsonValueNumber>(V));
    return Result;
}

static TArray<TSharedPtr<FJsonValue>> MatrixNumbers(const FMatrix& M)
{
    TArray<TSharedPtr<FJsonValue>> Result;
    for (int R = 0; R < 4; ++R) for (int C = 0; C < 4; ++C)
        Result.Add(MakeShared<FJsonValueNumber>(M.M[R][C]));
    return Result;
}

static TAutoConsoleVariable<int32> CVarFogEnabled(TEXT("r.UEFog.Enabled"), 1, TEXT("Enable the experimental fog pass."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<int32> CVarFogSteps(TEXT("r.UEFog.Steps"), 64, TEXT("Ray steps [1,1024]."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<int32> CVarFogDownsample(TEXT("r.UEFog.Downsample"), 1, TEXT("Fog resolution divisor [1,2]."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<int32> CVarFogReconstruction(TEXT("r.UEFog.Reconstruction"), 1, TEXT("0: bilinear, 1: depth-weighted."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<int32> CVarFogDebug(TEXT("r.UEFog.Debug"), 0, TEXT("0: composite, 1: transmittance, 2: scattering."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<int32> CVarFogDensityMode(TEXT("r.UEFog.DensityMode"), 1, TEXT("0: homogeneous sphere, 1: smooth radial, 2: procedural sine modulation."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> CVarFogExtinction(TEXT("r.UEFog.Extinction"), 0.35f, TEXT("Extinction in inverse metres."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> CVarFogEarlyOut(TEXT("r.UEFog.EarlyOut"), 0.0f, TEXT("Stop at T below this threshold; 0 disables."), ECVF_RenderThreadSafe);

class FUEFogIntegrateCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FUEFogIntegrateCS);
    SHADER_USE_PARAMETER_STRUCT(FUEFogIntegrateCS, FGlobalShader);
    class FManualLighting : SHADER_PERMUTATION_BOOL("UEFOG_MANUAL_LIGHTING");
    using FPermutationDomain = TShaderPermutationDomain<FManualLighting>;
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER(FIntPoint, FogSize)
        SHADER_PARAMETER(FIntPoint, ViewMin)
        SHADER_PARAMETER(FIntPoint, ViewSize)
        SHADER_PARAMETER(FMatrix44f, ClipToTranslatedWorld)
        SHADER_PARAMETER(FVector3f, CameraTranslated)
        SHADER_PARAMETER(FVector3f, SphereTranslated)
        SHADER_PARAMETER(float, SphereRadiusCm)
        SHADER_PARAMETER(float, ExtinctionPerMetre)
        SHADER_PARAMETER(float, EarlyOutThreshold)
        SHADER_PARAMETER(uint32, StepCount)
        SHADER_PARAMETER(uint32, DensityMode)
        SHADER_PARAMETER(float, ScatteringAlbedo)
        SHADER_PARAMETER(float, HGg)
        SHADER_PARAMETER(FVector3f, IncidentWeightRGB)
        SHADER_PARAMETER(FVector3f, PhotonDirectionWorld)
        SHADER_PARAMETER_RDG_TEXTURE_SRV(Texture2D<float>, SceneDepthTexture)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float4>, OutFog)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float>, OutRayDepth)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& Parameters)
    {
        return IsFeatureLevelSupported(Parameters.Platform, ERHIFeatureLevel::SM5);
    }
};

class FUEFogCompositePS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FUEFogCompositePS);
    SHADER_USE_PARAMETER_STRUCT(FUEFogCompositePS, FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER_STRUCT_REF(FViewUniformShaderParameters, View)
        SHADER_PARAMETER(FIntPoint, FogSize)
        SHADER_PARAMETER(FIntPoint, ViewMin)
        SHADER_PARAMETER(FIntPoint, ViewSize)
        SHADER_PARAMETER(FMatrix44f, ClipToTranslatedWorld)
        SHADER_PARAMETER(FVector3f, CameraTranslated)
        SHADER_PARAMETER(uint32, Reconstruction)
        SHADER_PARAMETER(uint32, DebugMode)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, SceneColorTexture)
        SHADER_PARAMETER_RDG_TEXTURE_SRV(Texture2D<float>, SceneDepthTexture)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, FogTexture)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, RayDepthTexture)
        RENDER_TARGET_BINDING_SLOTS()
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& Parameters)
    {
        return IsFeatureLevelSupported(Parameters.Platform, ERHIFeatureLevel::SM5);
    }
};

class FUEFogDiagnosticsCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FUEFogDiagnosticsCS);
    SHADER_USE_PARAMETER_STRUCT(FUEFogDiagnosticsCS, FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER_STRUCT_REF(FViewUniformShaderParameters, View)
        SHADER_PARAMETER(FIntPoint, ViewMin)
        SHADER_PARAMETER(FIntPoint, ViewSize)
        SHADER_PARAMETER(FIntPoint, FogSize)
        SHADER_PARAMETER(uint32, Reconstruction)
        SHADER_PARAMETER(FMatrix44f, ClipToTranslatedWorld)
        SHADER_PARAMETER(FVector3f, CameraTranslated)
        SHADER_PARAMETER_RDG_TEXTURE_SRV(Texture2D<float>, SceneDepthTexture)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float4>, OutSceneData)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float4>, OutReconstructed)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, FogTexture)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, RayDepthTexture)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& Parameters)
    { return IsFeatureLevelSupported(Parameters.Platform, ERHIFeatureLevel::SM5); }
};

IMPLEMENT_GLOBAL_SHADER(FUEFogIntegrateCS, "/Plugin/UEFog/Private/UEFog.usf", "IntegrateCS", SF_Compute);
IMPLEMENT_GLOBAL_SHADER(FUEFogCompositePS, "/Plugin/UEFog/Private/UEFog.usf", "CompositePS", SF_Pixel);
IMPLEMENT_GLOBAL_SHADER(FUEFogDiagnosticsCS, "/Plugin/UEFog/Private/UEFog.usf", "DiagnosticsCS", SF_Compute);

FUEFogViewExtension::FUEFogViewExtension(const FAutoRegister& AutoRegister)
    : FSceneViewExtensionBase(AutoRegister) {}

void FUEFogViewExtension::SetupViewFamily(FSceneViewFamily& Family)
{
    if (IsUEFogCompareActive())
    {
        Family.EngineShowFlags.SetLocalExposure(false);
        Family.EngineShowFlags.SetFog(true);
        Family.EngineShowFlags.SetVolumetricFog(true);
    }
}

void FUEFogViewExtension::SubscribeToPostProcessingPass(EPostProcessingPass Pass, const FSceneView& View,
    FPostProcessingPassDelegateArray& Callbacks, bool bIsPassEnabled)
{
    // The desktop before-DOF callback remains available when DOF itself is disabled.
    // ES3.1/mobile support is deliberately not advertised by this initial spike.
    if (Pass == EPostProcessingPass::BeforeDOF && View.bIsGameView && View.GetFeatureLevel() >= ERHIFeatureLevel::SM5)
    {
        Callbacks.Add(FPostProcessingPassDelegate::CreateRaw(this, &FUEFogViewExtension::RenderFog));
    }
}

FScreenPassTexture FUEFogViewExtension::RenderFog(FRDGBuilder& GraphBuilder, const FSceneView& View,
    const FPostProcessMaterialInputs& Inputs)
{
    Readback.Poll();
    if (IsUEFogCompareActive())
    {
        const auto Observed = FScreenPassTexture::CopyFromSlice(GraphBuilder, Inputs.GetInput(EPostProcessMaterialInput::SceneColor));
        if (Observed.IsValid()) ObserveUEFogCompareView(GraphBuilder, View, Observed.ViewRect, Observed.Texture->Desc.Extent);
    }
    if (!CVarFogEnabled.GetValueOnRenderThread() || !Inputs.SceneTextures.SceneTextures)
    {
        return Inputs.ReturnUntouchedSceneColorForPostProcessing(GraphBuilder);
    }
    FUEFogLighting Lighting;
    FString LightingError;
    if (!GetUEFogLightingSnapshot(Lighting, LightingError))
    {
        UE_LOG(LogTemp, Error, TEXT("UEFog render rejected: %s"), *LightingError);
        FPlatformMisc::RequestExitWithStatus(false, 2);
        return Inputs.ReturnUntouchedSceneColorForPostProcessing(GraphBuilder);
    }
    const FScreenPassTexture SceneColor = FScreenPassTexture::CopyFromSlice(GraphBuilder, Inputs.GetInput(EPostProcessMaterialInput::SceneColor));
    const FRDGTextureRef Depth = Inputs.SceneTextures.SceneTextures->GetParameters()->SceneDepthTexture;
    if (!SceneColor.IsValid() || !Depth)
    {
        return Inputs.ReturnUntouchedSceneColorForPostProcessing(GraphBuilder);
    }
    // Declare the depth plane only. Reading the entire depth/stencil resource
    // unnecessarily includes stencil and can interfere with stencil-write states.
    const FRDGTextureSRVRef DepthSRV = GraphBuilder.CreateSRV(
        FRDGTextureSRVDesc::CreateForMetaData(Depth, ERDGTextureMetaDataAccess::Depth));
    const FIntPoint ViewSize = SceneColor.ViewRect.Size();
    const int32 Divisor = FMath::Clamp(CVarFogDownsample.GetValueOnRenderThread(), 1, 2);
    const FIntPoint FogSize(FMath::DivideAndRoundUp(ViewSize.X, Divisor), FMath::DivideAndRoundUp(ViewSize.Y, Divisor));
    FRDGTextureRef Fog = GraphBuilder.CreateTexture(FRDGTextureDesc::Create2D(FogSize, PF_FloatRGBA,
        FClearValueBinding::None, TexCreate_ShaderResource | TexCreate_UAV), TEXT("UEFog.ScatteringTransmittance"));
    FRDGTextureRef RayDepth = GraphBuilder.CreateTexture(FRDGTextureDesc::Create2D(FogSize, PF_R32_FLOAT,
        FClearValueBinding::None, TexCreate_ShaderResource | TexCreate_UAV), TEXT("UEFog.RayDepth"));
    const FMatrix44f ClipToWorld(View.ViewMatrices.GetClipToTranslatedWorld());
    const FVector Translation = View.ViewMatrices.GetPreViewTranslation();
    const FVector3f Camera(View.ViewMatrices.GetViewOrigin() + Translation);

    auto* Integrate = GraphBuilder.AllocParameters<FUEFogIntegrateCS::FParameters>();
    Integrate->FogSize = FogSize;
    Integrate->ViewMin = SceneColor.ViewRect.Min;
    Integrate->ViewSize = ViewSize;
    Integrate->ClipToTranslatedWorld = ClipToWorld;
    Integrate->CameraTranslated = Camera;
    float OriginOffset = 0;
    FParse::Value(FCommandLine::Get(), TEXT("FogLabOriginOffset="), OriginOffset);
    Integrate->SphereTranslated = FVector3f(FVector(300 + OriginOffset, 0, 180) + Translation);
    Integrate->SphereRadiusCm = 360.0f;
    Integrate->ExtinctionPerMetre = FMath::Max(0.0f, CVarFogExtinction.GetValueOnRenderThread());
    Integrate->EarlyOutThreshold = FMath::Clamp(CVarFogEarlyOut.GetValueOnRenderThread(), 0.0f, 0.99f);
    Integrate->StepCount = FMath::Clamp(CVarFogSteps.GetValueOnRenderThread(), 1, 1024);
    Integrate->DensityMode = FMath::Clamp(CVarFogDensityMode.GetValueOnRenderThread(), 0, 2);
    Integrate->ScatteringAlbedo = Lighting.Albedo;
    Integrate->HGg = Lighting.G;
    Integrate->IncidentWeightRGB = Lighting.IncidentWeight;
    Integrate->PhotonDirectionWorld = Lighting.PhotonDirection;
    Integrate->SceneDepthTexture = DepthSRV;
    Integrate->OutFog = GraphBuilder.CreateUAV(Fog);
    Integrate->OutRayDepth = GraphBuilder.CreateUAV(RayDepth);
    FUEFogIntegrateCS::FPermutationDomain Permutation;
    Permutation.Set<FUEFogIntegrateCS::FManualLighting>(Lighting.Model == 1);
    TShaderMapRef<FUEFogIntegrateCS> IntegrateShader(GetGlobalShaderMap(View.GetFeatureLevel()), Permutation);
    if (IsUEFogCompareActive())
    {
        ClearUnusedGraphResources(IntegrateShader, Integrate);
        GraphBuilder.AddPass(RDG_EVENT_NAME("UEFog Integrate %dx%d Steps=%u", FogSize.X, FogSize.Y, Integrate->StepCount), Integrate, ERDGPassFlags::Compute,
            [IntegrateShader, Integrate, FogSize, Frame = View.Family->FrameNumber](FRHIComputeCommandList& RHICmdList)
            {
                FComputeShaderUtils::Dispatch(RHICmdList, IntegrateShader, *Integrate, FComputeShaderUtils::GetGroupCount(FogSize, FIntPoint(8,8)));
                CountUEFogComparePass(Frame, false);
            });
    }
    else FComputeShaderUtils::AddPass(GraphBuilder, RDG_EVENT_NAME("UEFog Integrate %dx%d Steps=%u", FogSize.X, FogSize.Y, Integrate->StepCount),
        IntegrateShader, Integrate, FComputeShaderUtils::GetGroupCount(FogSize, FIntPoint(8, 8)));

    if (Readback.WantsFrame())
    {
        auto M = Readback.Metadata;
        M->SetNumberField(TEXT("engineFrame"), View.Family->FrameNumber);
        FString Fixture = TEXT("Diagnostic"), CameraMode = TEXT("Outside");
        FParse::Value(FCommandLine::Get(), TEXT("FogLabFixture="), Fixture);
        FParse::Value(FCommandLine::Get(), TEXT("FogLabCamera="), CameraMode);
        M->SetStringField(TEXT("fixtureKey"), Fixture);
        M->SetStringField(TEXT("cameraMode"), CameraMode);
        M->SetNumberField(TEXT("originOffsetCm"), OriginOffset);
        M->SetStringField(TEXT("rhi"), GDynamicRHI->GetName());
        M->SetStringField(TEXT("depthFormat"), GPixelFormats[Depth->Desc.Format].Name);
        M->SetStringField(TEXT("depthConvention"), TEXT("reversed Z; <=1e-7 is sky; ray distances in cm"));
        if (Lighting.Model == 1)
        {
            M->SetNumberField(TEXT("schemaVersion"), 2);
            M->SetStringField(TEXT("modelKey"), TEXT("ManualDirectionalHG-v1"));
            M->SetStringField(TEXT("model"), TEXT("manual directional HG; light path factors 1; no UE lighting or volume shadows"));
            M->SetStringField(TEXT("lightingInput"), TEXT("manual"));
            M->SetStringField(TEXT("directionConvention"), TEXT("photon-propagation-to-camera-v1"));
            M->SetNumberField(TEXT("albedo"), Lighting.Albedo);
            M->SetNumberField(TEXT("g"), Lighting.G);
            M->SetArrayField(TEXT("incidentWeightRGB"), Numbers({Lighting.IncidentWeight.X, Lighting.IncidentWeight.Y, Lighting.IncidentWeight.Z}));
            M->SetArrayField(TEXT("photonDirectionWorld"), Numbers({Lighting.PhotonDirection.X, Lighting.PhotonDirection.Y, Lighting.PhotonDirection.Z}));
            M->SetNumberField(TEXT("lightTransmittance"), 1);
            M->SetNumberField(TEXT("lightVisibility"), 1);
        }
        else
        {
            M->SetStringField(TEXT("model"), TEXT("prescribed constant J; no lighting; cm positions, m^-1 extinction"));
            M->SetArrayField(TEXT("sourceFunction"), Numbers({.35,.60,.90}));
        }
        M->SetArrayField(TEXT("sceneExtent"), Numbers({double(SceneColor.Texture->Desc.Extent.X), double(SceneColor.Texture->Desc.Extent.Y)}));
        M->SetArrayField(TEXT("depthExtent"), Numbers({double(Depth->Desc.Extent.X), double(Depth->Desc.Extent.Y)}));
        M->SetArrayField(TEXT("viewMin"), Numbers({double(SceneColor.ViewRect.Min.X), double(SceneColor.ViewRect.Min.Y)}));
        M->SetArrayField(TEXT("viewSize"), Numbers({double(ViewSize.X), double(ViewSize.Y)}));
        M->SetArrayField(TEXT("fogSize"), Numbers({double(FogSize.X), double(FogSize.Y)}));
        const FVector Eye = View.ViewMatrices.GetViewOrigin();
        M->SetArrayField(TEXT("cameraWorldCm"), Numbers({Eye.X, Eye.Y, Eye.Z}));
        M->SetArrayField(TEXT("preViewTranslationCm"), Numbers({Translation.X, Translation.Y, Translation.Z}));
        M->SetArrayField(TEXT("sphereWorldCm"), Numbers({300.0 + OriginOffset, 0, 180}));
        M->SetNumberField(TEXT("sphereRadiusCm"), 360);
        const FVector Right = View.GetViewRight(), Up = View.GetViewUp(), Forward = View.GetViewDirection();
        M->SetArrayField(TEXT("cameraRight"), Numbers({Right.X,Right.Y,Right.Z}));
        M->SetArrayField(TEXT("cameraUp"), Numbers({Up.X,Up.Y,Up.Z}));
        M->SetArrayField(TEXT("cameraForward"), Numbers({Forward.X,Forward.Y,Forward.Z}));
        M->SetArrayField(TEXT("viewToClip"), MatrixNumbers(View.ViewMatrices.GetViewToClip()));
        M->SetArrayField(TEXT("clipToTranslatedWorld"), MatrixNumbers(FMatrix(ClipToWorld)));
        M->SetNumberField(TEXT("steps"), Integrate->StepCount);
        M->SetNumberField(TEXT("densityMode"), Integrate->DensityMode);
        M->SetNumberField(TEXT("extinctionPerMetre"), Integrate->ExtinctionPerMetre);
        M->SetNumberField(TEXT("earlyOut"), Integrate->EarlyOutThreshold);
        M->SetNumberField(TEXT("downsample"), Divisor);
        M->SetNumberField(TEXT("reconstruction"), CVarFogReconstruction.GetValueOnRenderThread() != 0);
        M->SetBoolField(TEXT("scatteringPreExposed"), false);
        M->SetStringField(TEXT("pixelMapping"), TEXT("UV=(fogPixel+0.5)/fogSize; depthPixel=viewMin+floor(UV*viewSize); row-major little-endian packed files"));
        auto SceneData = GraphBuilder.CreateTexture(FRDGTextureDesc::Create2D(ViewSize, PF_A32B32G32R32F,
            FClearValueBinding::None, TexCreate_ShaderResource | TexCreate_UAV), TEXT("UEFog.NumericSceneData"));
        auto Reconstructed = GraphBuilder.CreateTexture(FRDGTextureDesc::Create2D(ViewSize, PF_A32B32G32R32F,
            FClearValueBinding::None, TexCreate_ShaderResource | TexCreate_UAV), TEXT("UEFog.NumericReconstructedST"));
        auto* P = GraphBuilder.AllocParameters<FUEFogDiagnosticsCS::FParameters>();
        P->View = View.ViewUniformBuffer;
        P->ViewMin = SceneColor.ViewRect.Min; P->ViewSize = ViewSize;
        P->FogSize = FogSize;
        P->Reconstruction = CVarFogReconstruction.GetValueOnRenderThread() != 0;
        P->FogTexture = Fog; P->RayDepthTexture = RayDepth;
        P->ClipToTranslatedWorld = ClipToWorld; P->CameraTranslated = Camera;
        P->SceneDepthTexture = DepthSRV; P->OutSceneData = GraphBuilder.CreateUAV(SceneData);
        P->OutReconstructed = GraphBuilder.CreateUAV(Reconstructed);
        TShaderMapRef<FUEFogDiagnosticsCS> Shader(GetGlobalShaderMap(View.GetFeatureLevel()));
        FComputeShaderUtils::AddPass(GraphBuilder, RDG_EVENT_NAME("UEFog NumericDiagnostics"), Shader, P,
            FComputeShaderUtils::GetGroupCount(ViewSize, FIntPoint(8,8)));
        Readback.Add(GraphBuilder, Fog, TEXT("integrated-st"));
        Readback.Add(GraphBuilder, RayDepth, TEXT("fog-ray-depth"));
        Readback.Add(GraphBuilder, SceneData, TEXT("scene-data"));
        Readback.Add(GraphBuilder, Reconstructed, TEXT("reconstructed-st"));
        M->SetStringField(TEXT("reconstructionObservation"), TEXT("float32 GPU output of ReconstructFog; same HLSL function/centres/inputs as CompositePS, before pre-exposure"));
    }

    FScreenPassRenderTarget Output = Inputs.OverrideOutput;
    if (!Output.IsValid())
    {
        Output = FScreenPassRenderTarget::CreateFromInput(GraphBuilder, SceneColor, ERenderTargetLoadAction::ENoAction, TEXT("UEFog.Composite"));
    }
    auto* Composite = GraphBuilder.AllocParameters<FUEFogCompositePS::FParameters>();
    Composite->View = View.ViewUniformBuffer;
    Composite->FogSize = FogSize;
    Composite->ViewMin = SceneColor.ViewRect.Min;
    Composite->ViewSize = ViewSize;
    Composite->ClipToTranslatedWorld = ClipToWorld;
    Composite->CameraTranslated = Camera;
    Composite->Reconstruction = CVarFogReconstruction.GetValueOnRenderThread() != 0;
    Composite->DebugMode = FMath::Clamp(CVarFogDebug.GetValueOnRenderThread(), 0, 2);
    Composite->SceneColorTexture = SceneColor.Texture;
    Composite->SceneDepthTexture = DepthSRV;
    Composite->FogTexture = Fog;
    Composite->RayDepthTexture = RayDepth;
    Composite->RenderTargets[0] = Output.GetRenderTargetBinding();
    TShaderMapRef<FUEFogCompositePS> CompositeShader(GetGlobalShaderMap(View.GetFeatureLevel()));
    if (IsUEFogCompareActive())
    {
        TShaderMapRef<FScreenPassVS> VS(GetGlobalShaderMap(View.GetFeatureLevel()));
        const FScreenPassPipelineState Pipeline(VS, CompositeShader, TStaticBlendState<>::GetRHI(), TStaticDepthStencilState<false, CF_Always>::GetRHI());
        ClearUnusedGraphResources(CompositeShader, Composite);
        AddDrawScreenPass(GraphBuilder, RDG_EVENT_NAME("UEFog ReconstructAndComposite"), View,
            FScreenPassTextureViewport(Output), FScreenPassTextureViewport(SceneColor), Pipeline, Composite,
            [CompositeShader, Composite, Frame = View.Family->FrameNumber](FRHICommandList& RHICmdList)
            {
                SetShaderParameters(RHICmdList, CompositeShader, CompositeShader.GetPixelShader(), *Composite);
                CountUEFogComparePass(Frame, true);
            });
    }
    else AddDrawScreenPass(GraphBuilder, RDG_EVENT_NAME("UEFog ReconstructAndComposite"), View,
        FScreenPassTextureViewport(Output), FScreenPassTextureViewport(SceneColor), CompositeShader, Composite);
    return Output;
}
