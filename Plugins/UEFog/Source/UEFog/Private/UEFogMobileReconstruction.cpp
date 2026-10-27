#include "UEFogMobileReconstruction.h"
#include "UEFogMobilePerformance.h"
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

#define RECON_PARAMETERS \
    SHADER_PARAMETER_STRUCT_REF(FViewUniformShaderParameters, View) \
    SHADER_PARAMETER_RDG_UNIFORM_BUFFER(FMobileSceneTextureUniformParameters, MobileSceneTextures) \
    SHADER_PARAMETER(FMatrix44f, ReconClipToTranslatedWorld) \
    SHADER_PARAMETER(FVector3f, ReconCameraTranslated) \
    SHADER_PARAMETER(FVector3f, ReconSphereTranslated) \
    SHADER_PARAMETER(float, ReconSigma) \
    SHADER_PARAMETER(float, ReconNearCm) \
    SHADER_PARAMETER(float, ReconProjectionSignY) \
    SHADER_PARAMETER(uint32, ReconTagA) \
    SHADER_PARAMETER(uint32, ReconTagB) \
    SHADER_PARAMETER(uint32, ReconFrame) \
    SHADER_PARAMETER(uint32, ReconProfile) \
    SHADER_PARAMETER(uint32, ReconDiagnostic) \
    SHADER_PARAMETER(FIntPoint, ReconViewMin) \
    SHADER_PARAMETER(FIntPoint, ReconViewSize) \
    SHADER_PARAMETER(FIntPoint, ReconFogSize) \
    SHADER_PARAMETER(FIntPoint, ReconSceneExtent)

class FReconKnownWriteCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FReconKnownWriteCS); SHADER_USE_PARAMETER_STRUCT(FReconKnownWriteCS,FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters,)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float>,ReconProbeOutput)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P) { return P.Platform==SP_VULKAN_ES3_1_ANDROID; }
};
class FReconKnownObserveCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FReconKnownObserveCS); SHADER_USE_PARAMETER_STRUCT(FReconKnownObserveCS,FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters,)
        RECON_PARAMETERS
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float>,ReconProbeInput)
        SHADER_PARAMETER_RDG_BUFFER_UAV(RWStructuredBuffer<float4>,ReconSamples)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P) { return P.Platform==SP_VULKAN_ES3_1_ANDROID; }
};
class FReconIntegrateCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FReconIntegrateCS); SHADER_USE_PARAMETER_STRUCT(FReconIntegrateCS,FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters,)
        RECON_PARAMETERS
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float4>,ReconLowOutput)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float>,ReconGuideOutput)
        SHADER_PARAMETER_RDG_BUFFER_UAV(RWStructuredBuffer<int2>,ReconAuxOutput)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P) { return P.Platform==SP_VULKAN_ES3_1_ANDROID; }
};
class FReconReconstructCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FReconReconstructCS); SHADER_USE_PARAMETER_STRUCT(FReconReconstructCS,FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters,)
        RECON_PARAMETERS
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float4>,ReconLowInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float>,ReconGuideInput)
        SHADER_PARAMETER_RDG_TEXTURE_UAV(RWTexture2D<float4>,ReconOutput)
        SHADER_PARAMETER_RDG_BUFFER_UAV(RWStructuredBuffer<int2>,ReconAuxOutput)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P) { return P.Platform==SP_VULKAN_ES3_1_ANDROID; }
};
class FReconCompositePS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FReconCompositePS); SHADER_USE_PARAMETER_STRUCT(FReconCompositePS,FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters,)
        RECON_PARAMETERS
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float4>,ReconInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D,ReconCin)
        RENDER_TARGET_BINDING_SLOTS()
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P) { return P.Platform==SP_VULKAN_ES3_1_ANDROID; }
};
class FReconDenseObserveCS final : public FGlobalShader
{
public:
    DECLARE_GLOBAL_SHADER(FReconDenseObserveCS); SHADER_USE_PARAMETER_STRUCT(FReconDenseObserveCS,FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters,)
        RECON_PARAMETERS
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float>,ReconProbeInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float4>,ReconLowInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float>,ReconGuideInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D<float4>,ReconInput)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D,ReconCin)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D,ReconConsumed)
        SHADER_PARAMETER_RDG_BUFFER_SRV(StructuredBuffer<int2>,ReconAuxInput)
        SHADER_PARAMETER_RDG_BUFFER_UAV(RWStructuredBuffer<float4>,ReconSamples)
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P) { return P.Platform==SP_VULKAN_ES3_1_ANDROID; }
};
IMPLEMENT_GLOBAL_SHADER(FReconKnownWriteCS,"/Plugin/UEFog/Private/UEFogMobileReconstruction.usf","WriteKnownGuideCS",SF_Compute);
IMPLEMENT_GLOBAL_SHADER(FReconKnownObserveCS,"/Plugin/UEFog/Private/UEFogMobileReconstruction.usf","ObserveKnownGuideCS",SF_Compute);
IMPLEMENT_GLOBAL_SHADER(FReconIntegrateCS,"/Plugin/UEFog/Private/UEFogMobileReconstruction.usf","IntegrateReconCS",SF_Compute);
IMPLEMENT_GLOBAL_SHADER(FReconReconstructCS,"/Plugin/UEFog/Private/UEFogMobileReconstruction.usf","ReconstructSTCS",SF_Compute);
IMPLEMENT_GLOBAL_SHADER(FReconCompositePS,"/Plugin/UEFog/Private/UEFogMobileReconstruction.usf","CompositeReconPS",SF_Pixel);
IMPLEMENT_GLOBAL_SHADER(FReconDenseObserveCS,"/Plugin/UEFog/Private/UEFogMobileReconstruction.usf","ObserveDenseReconCS",SF_Compute);
#undef RECON_PARAMETERS

#if PLATFORM_ANDROID
namespace MobileRecon
{
struct FFrame
{
    uint32 Number=0;
    FScreenPassTexture Cin,Low,Guide,Reconstructed,Returned;
    FRDGTextureRef Probe=nullptr;
    FRDGBufferRef Aux=nullptr;
    uint32 Integrate=0,Reconstruct=0,Composite=0;
};
struct FState
{
    FString Profile,Grid,FixtureName,RunId,Directory,BuildId,Source;
    uint32 Code=0,TagA=0,TagB=0,MobileCallbacks=0,ProducerCallbacks=0,ObserverCallbacks=0,Bytes=0;
    int32 Near=600;
    double Sigma=.35;
    bool Diagnostics=false,Scheduled=false,Finished=false;
    const FSceneInterface* Scene=nullptr;
    std::atomic<bool> Ready{false};
    TSharedPtr<FJsonObject> Fixture,Record;
    FFrame Current;
    TUniquePtr<FRHIGPUBufferReadback> Readback;
};
TUniquePtr<FState> State;
TArray<TSharedPtr<FJsonValue>> Numbers(std::initializer_list<double> Values)
{
    TArray<TSharedPtr<FJsonValue>> Out; for(double X:Values) Out.Add(MakeShared<FJsonValueNumber>(X)); return Out;
}
TArray<TSharedPtr<FJsonValue>> Matrix(const FMatrix& M)
{
    TArray<TSharedPtr<FJsonValue>> Out;
    for(int R=0;R<4;++R) for(int C=0;C<4;++C) Out.Add(MakeShared<FJsonValueNumber>(M.M[R][C])); return Out;
}
FIntPoint FogSize(FIntPoint View)
{
    return State->Code==1 ? View : State->Grid==TEXT("Odd") ? FIntPoint(775,359) : FIntPoint(776,360);
}
bool Control() { return State->Code==0 || State->Code==4; }
TSharedRef<FJsonObject> Desc(FRDGTextureRef Texture)
{
    auto J=MakeShared<FJsonObject>();J->SetBoolField(TEXT("valid"),Texture!=nullptr);
    if(Texture)
    {
        const auto& P=GPixelFormats[Texture->Desc.Format];
        J->SetStringField(TEXT("name"),Texture->Name);J->SetStringField(TEXT("format"),P.Name);
        J->SetNumberField(TEXT("platformFormat"),P.PlatformFormat);J->SetNumberField(TEXT("flags"),double(uint64(Texture->Desc.Flags)));
        J->SetArrayField(TEXT("extent"),Numbers({double(Texture->Desc.Extent.X),double(Texture->Desc.Extent.Y)}));
        J->SetNumberField(TEXT("samples"),Texture->Desc.NumSamples);
        J->SetBoolField(TEXT("memoryless"),EnumHasAnyFlags(Texture->Desc.Flags,TexCreate_Memoryless));
        J->SetBoolField(TEXT("uavSupported"),EnumHasAllFlags(P.Capabilities,EPixelFormatCapabilities::UAV));
        J->SetBoolField(TEXT("sampleSupported"),EnumHasAllFlags(P.Capabilities,EPixelFormatCapabilities::TextureSample));
        J->SetBoolField(TEXT("rtSupported"),EnumHasAllFlags(P.Capabilities,EPixelFormatCapabilities::RenderTarget));
    }
    return J;
}
void Save()
{
    if(!State->Record || State->Directory.IsEmpty()) return;
    State->Record->SetNumberField(TEXT("mobilePrePostCallbacks"),State->MobileCallbacks);
    State->Record->SetNumberField(TEXT("producerCallbacks"),State->ProducerCallbacks);
    State->Record->SetNumberField(TEXT("observerCallbacks"),State->ObserverCallbacks);
    FString Text;FJsonSerializer::Serialize(State->Record.ToSharedRef(),TJsonWriterFactory<>::Create(&Text));
    if(!FFileHelper::SaveStringToFile(Text,*FPaths::Combine(State->Directory,TEXT("reconstruction.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
        UE_LOG(LogTemp,Error,TEXT("Mobile reconstruction metadata write failed"));
}
TSharedRef<FJsonObject> Snapshot(const FSceneView& V,FScreenPassTexture Color,TRDGUniformBufferRef<FMobileSceneTextureUniformParameters> Mobile)
{
    auto J=MakeShared<FJsonObject>();const FIntPoint Size=Color.ViewRect.Size(),F=FogSize(Size),A=FIntPoint::ZeroValue;
    J->SetStringField(TEXT("kind"),TEXT("UEFogLab.MobileReconstruction"));J->SetNumberField(TEXT("schemaVersion"),1);
    J->SetStringField(TEXT("modelKey"),TEXT("MobileUniformReconstruction64-v1"));J->SetStringField(TEXT("profile"),State->Profile);
    J->SetStringField(TEXT("grid"),State->Code==1?TEXT("Full"):*State->Grid);
    J->SetStringField(TEXT("fixture"),State->FixtureName);J->SetObjectField(TEXT("fixtureDescription"),State->Fixture.ToSharedRef());
    J->SetStringField(TEXT("runId"),State->RunId);J->SetNumberField(TEXT("pid"),FPlatformProcess::GetCurrentProcessId());
    J->SetStringField(TEXT("buildId"),State->BuildId);J->SetStringField(TEXT("sourceRevision"),State->Source);
    J->SetStringField(TEXT("rhi"),GDynamicRHI->GetName());J->SetStringField(TEXT("featureLevel"),V.GetFeatureLevel()==ERHIFeatureLevel::ES3_1?TEXT("ES3_1"):TEXT("other"));
    J->SetStringField(TEXT("shaderFormat"),LegacyShaderPlatformToShaderFormat(V.GetShaderPlatform()).ToString());
    J->SetStringField(TEXT("producerCallback"),TEXT("Mobile BeforeDOF.ReconProducer"));J->SetStringField(TEXT("observerCallback"),TEXT("Mobile BeforeDOF.ReconObserver"));
    J->SetStringField(TEXT("colorStage"),TEXT("actual low ST/R32 guide -> unexposed reconstructed ST -> actual HDR PS return -> engine consumer before bloom/tonemap"));
    J->SetNumberField(TEXT("frame"),V.Family->FrameNumber);J->SetNumberField(TEXT("nearCm"),State->Near);
    J->SetNumberField(TEXT("extinctionPerMetre"),State->Sigma);J->SetNumberField(TEXT("preExposure"),V.State?V.State->GetPreExposure():-1);
    J->SetNumberField(TEXT("steps"),64);J->SetNumberField(TEXT("earlyOut"),0);J->SetArrayField(TEXT("sourceJ"),Numbers({.35,.6,.9}));
    J->SetArrayField(TEXT("sphereCentreCm"),Numbers({800,0,0}));J->SetNumberField(TEXT("sphereRadiusCm"),400);
    const FVector Origin=V.ViewMatrices.GetViewOrigin();J->SetArrayField(TEXT("cameraCm"),Numbers({Origin.X,Origin.Y,Origin.Z}));
    J->SetArrayField(TEXT("viewMin"),Numbers({double(Color.ViewRect.Min.X),double(Color.ViewRect.Min.Y)}));
    J->SetArrayField(TEXT("viewSize"),Numbers({double(Size.X),double(Size.Y)}));J->SetArrayField(TEXT("fogSize"),Numbers({double(F.X),double(F.Y)}));
    J->SetArrayField(TEXT("allocatedFogSize"),Numbers({double(A.X),double(A.Y)}));
    J->SetArrayField(TEXT("allocatedReconstructionSize"),Numbers({0,0}));
    J->SetArrayField(TEXT("surfaceSize"),Numbers({double(V.Family->RenderTarget->GetSizeXY().X),double(V.Family->RenderTarget->GetSizeXY().Y)}));
    J->SetArrayField(TEXT("worldToClip"),Matrix(V.ViewMatrices.GetWorldToClip()));J->SetArrayField(TEXT("clipToWorld"),Matrix(V.ViewMatrices.GetClipToWorld()));
    J->SetNumberField(TEXT("projectionSignY"),GProjectionSignY);J->SetObjectField(TEXT("sceneColorInput"),Desc(Color.Texture));
    J->SetObjectField(TEXT("sceneDepth"),Desc(Mobile->GetContents()->SceneDepthTexture));
    J->SetStringField(TEXT("pixelSelection"),TEXT("min(V-1, ((2*f+1)*V)//(2*F)); nominal UV=(f+.5)/F"));
    J->SetStringField(TEXT("guideUnits"),TEXT("view-axis cm; sky0; R32_FLOAT"));
    for(const TCHAR* K:{TEXT("integrateDispatches"),TEXT("reconstructDispatches"),TEXT("compositePasses"),TEXT("sampleDispatches"),TEXT("readbackRequests"),TEXT("probeWriteDispatches"),TEXT("probeReadDispatches")})J->SetNumberField(K,0);
    J->SetBoolField(TEXT("gpuCompletionConfirmed"),false);J->SetBoolField(TEXT("overrideOutputActive"),false);
    J->SetNumberField(TEXT("oldFogPasses"),0); // shader-platform/CVar exclusion; not profiler measurement
    auto C=MakeShared<FJsonObject>();
    for(const TCHAR* Name:{TEXT("r.Mobile.ShadingPath"),TEXT("r.MobileHDR"),TEXT("r.Mobile.AntiAliasing"),TEXT("r.MSAACount"),TEXT("r.DynamicRes.OperationMode"),TEXT("r.Mobile.ForceDepthResolve"),TEXT("r.UEFog.Enabled"),TEXT("r.Fog"),TEXT("r.VolumetricFog"),TEXT("r.LocalFogVolume"),TEXT("r.SupportLocalFogVolumes")})
    {const auto* Value=IConsoleManager::Get().FindConsoleVariable(Name);C->SetNumberField(Name,Value?Value->GetInt():-1);}
    J->SetObjectField(TEXT("cvars"),C);return J;
}
template<class T> void Common(T* P,const FSceneView& V,FScreenPassTexture Color,TRDGUniformBufferRef<FMobileSceneTextureUniformParameters> Mobile,bool Capture)
{
    const FVector Translation=V.ViewMatrices.GetPreViewTranslation();
    P->View=V.ViewUniformBuffer;P->MobileSceneTextures=Mobile;P->ReconClipToTranslatedWorld=FMatrix44f(V.ViewMatrices.GetClipToTranslatedWorld());
    P->ReconCameraTranslated=FVector3f(V.ViewMatrices.GetViewOrigin()+Translation);P->ReconSphereTranslated=FVector3f(FVector(800,0,0)+Translation);
    P->ReconSigma=float(State->Sigma);P->ReconNearCm=float(State->Near);P->ReconProjectionSignY=GProjectionSignY;
    P->ReconTagA=State->TagA;P->ReconTagB=State->TagB;P->ReconFrame=V.Family->FrameNumber;P->ReconProfile=State->Code;P->ReconDiagnostic=Capture?1:0;
    P->ReconViewMin=Color.ViewRect.Min;P->ReconViewSize=Color.ViewRect.Size();P->ReconFogSize=FogSize(Color.ViewRect.Size());P->ReconSceneExtent=Color.Texture->Desc.Extent;
}
class FExtension final : public FSceneViewExtensionBase
{
public:
    explicit FExtension(const FAutoRegister& Auto):FSceneViewExtensionBase(Auto){}
    bool Matches(const FSceneView& V)const
    {return State&&State->Ready.load(std::memory_order_acquire)&&V.bIsGameView&&V.Family->Scene==State->Scene&&V.Family->Views.Num()==1&&V.GetFeatureLevel()==ERHIFeatureLevel::ES3_1&&V.GetShaderPlatform()==SP_VULKAN_ES3_1_ANDROID;}
    virtual void PreRenderViewFamily_RenderThread(FRDGBuilder& Graph,FSceneViewFamily& Family)override
    {if(Family.Views.Num()==1&&Matches(*Family.Views[0]))UEFogMobilePerformanceBegin(Graph,*Family.Views[0]);}
    virtual void PostRenderViewFamily_RenderThread(FRDGBuilder& Graph,FSceneViewFamily& Family)override
    {if(Family.Views.Num()==1&&Matches(*Family.Views[0]))UEFogMobilePerformanceEnd(Graph,*Family.Views[0]);}
    virtual void PrePostProcessPassMobile_RenderThread(FRDGBuilder&,const FSceneView& V,const FMobilePostProcessingInputs&)override{if(Matches(V))++State->MobileCallbacks;}
    virtual void SubscribeToPostProcessingPass(EPostProcessingPass Pass,const FSceneView& V,FPostProcessingPassDelegateArray& Callbacks,bool)override
    {
        if(Pass==EPostProcessingPass::BeforeDOF&&Matches(V))
        {Callbacks.Add(FPostProcessingPassDelegate::CreateRaw(this,&FExtension::Produce));Callbacks.Add(FPostProcessingPassDelegate::CreateRaw(this,&FExtension::Observe));}
    }
    FScreenPassTexture Produce(FRDGBuilder& Graph,const FSceneView& V,const FPostProcessMaterialInputs& Inputs)
    {
        ++State->ProducerCallbacks;const auto Mobile=Inputs.SceneTextures.MobileSceneTextures.GetUniformBuffer();
        const auto Color=FScreenPassTexture::CopyFromSlice(Graph,Inputs.GetInput(EPostProcessMaterialInput::SceneColor));
        State->Current={};State->Current.Number=V.Family->FrameNumber;State->Current.Cin=Color;
        UEFogMobilePerformanceBoundary(Graph,1);
        if(!Mobile||!Color.IsValid()||State->Code==0)
        {
            UEFogMobilePerformanceBoundary(Graph,2);UEFogMobilePerformanceBoundary(Graph,3);UEFogMobilePerformanceBoundary(Graph,4);
            return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        }
        const bool Capture=State->Diagnostics&&State->ProducerCallbacks==180&&!State->Scheduled;
        const FIntPoint Size=Color.ViewRect.Size(),F=FogSize(Size);
        if(Size!=FIntPoint(1552,720)||Color.ViewRect.Min!=FIntPoint::ZeroValue||Inputs.OverrideOutput.IsValid()||
            !EnumHasAllFlags(GPixelFormats[PF_R32_FLOAT].Capabilities,EPixelFormatCapabilities::UAV|EPixelFormatCapabilities::TextureLoad)||
            (State->Code!=4&&!EnumHasAllFlags(GPixelFormats[PF_FloatRGBA].Capabilities,EPixelFormatCapabilities::UAV|EPixelFormatCapabilities::TextureLoad|EPixelFormatCapabilities::RenderTarget)))
        {
            if(!IsUEFogMobilePerformanceActive()){State->Record=Snapshot(V,Color,Mobile);State->Record->SetStringField(TEXT("status"),TEXT("unsupported-view-or-R32"));Save();}
            return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        }
        if(Capture)
        {
            State->Record=Snapshot(V,Color,Mobile);State->Record->SetStringField(TEXT("status"),TEXT("pending"));
            State->Current.Probe=Graph.CreateTexture(FRDGTextureDesc::Create2D(FIntPoint(3,1),PF_R32_FLOAT,FClearValueBinding::None,TexCreate_ShaderResource|TexCreate_UAV),TEXT("UEFog.Recon.KnownR32"));
            auto* P=Graph.AllocParameters<FReconKnownWriteCS::FParameters>();P->ReconProbeOutput=Graph.CreateUAV(State->Current.Probe);
            TShaderMapRef<FReconKnownWriteCS> Shader(GetGlobalShaderMap(V.GetFeatureLevel()));
            FComputeShaderUtils::AddPass(Graph,RDG_EVENT_NAME("UEFog.Recon.R32KnownWrite"),Shader,P,FIntVector(1,1,1));
            State->Record->SetObjectField(TEXT("probeTexture"),Desc(State->Current.Probe));State->Record->SetNumberField(TEXT("probeWriteDispatches"),1);
        }
        if(State->Code==4)return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        auto* Low=Graph.CreateTexture(FRDGTextureDesc::Create2D(F,PF_FloatRGBA,FClearValueBinding::None,TexCreate_ShaderResource|TexCreate_UAV),TEXT("UEFog.Recon.LowST"));
        auto* Guide=Graph.CreateTexture(FRDGTextureDesc::Create2D(F,PF_R32_FLOAT,FClearValueBinding::None,TexCreate_ShaderResource|TexCreate_UAV),TEXT("UEFog.Recon.LowGuideR32"));
        // Full writes its identity aux in integration; Half writes it in reconstruction.
        auto* Aux=Graph.CreateBuffer(FRDGBufferDesc::CreateStructuredDesc(sizeof(FIntPoint),Capture?uint32(Size.X*Size.Y):1),TEXT("UEFog.Recon.ActualAux"));
        auto* I=Graph.AllocParameters<FReconIntegrateCS::FParameters>();Common(I,V,Color,Mobile,Capture);
        I->ReconLowOutput=Graph.CreateUAV(Low);I->ReconGuideOutput=Graph.CreateUAV(Guide);I->ReconAuxOutput=Graph.CreateUAV(Aux);
        TShaderMapRef<FReconIntegrateCS> Integrate(GetGlobalShaderMap(V.GetFeatureLevel()));
        FComputeShaderUtils::AddPass(Graph,RDG_EVENT_NAME("UEFog.Recon.Integrate64"),Integrate,I,FComputeShaderUtils::GetGroupCount(F,FIntPoint(8,8)));
        State->Current.Integrate=1;UEFogMobilePerformanceBoundary(Graph,2);
        FRDGTextureRef Rec=Low;
        if(State->Code!=1)
        {
            Rec=Graph.CreateTexture(FRDGTextureDesc::Create2D(Size,PF_FloatRGBA,FClearValueBinding::None,TexCreate_ShaderResource|TexCreate_UAV),TEXT("UEFog.Recon.ReconstructedST"));
            auto* R=Graph.AllocParameters<FReconReconstructCS::FParameters>();Common(R,V,Color,Mobile,Capture);
            R->ReconLowInput=Low;R->ReconGuideInput=Guide;R->ReconOutput=Graph.CreateUAV(Rec);R->ReconAuxOutput=Graph.CreateUAV(Aux);
            TShaderMapRef<FReconReconstructCS> Reconstruct(GetGlobalShaderMap(V.GetFeatureLevel()));
            FComputeShaderUtils::AddPass(Graph,RDG_EVENT_NAME("UEFog.Recon.ReconstructUnexposedST"),Reconstruct,R,FComputeShaderUtils::GetGroupCount(Size,FIntPoint(8,8)));
            State->Current.Reconstruct=1;
        }
        UEFogMobilePerformanceBoundary(Graph,3);
        FScreenPassRenderTarget Output=Inputs.OverrideOutput;
        if(!Output.IsValid())
            Output=FScreenPassRenderTarget(Graph.CreateTexture(FRDGTextureDesc::Create2D(Color.Texture->Desc.Extent,PF_FloatRGBA,FClearValueBinding::None,TexCreate_ShaderResource|TexCreate_RenderTargetable),TEXT("UEFog.Recon.ReturnedHDR")),Color.ViewRect,ERenderTargetLoadAction::ENoAction);
        auto* P=Graph.AllocParameters<FReconCompositePS::FParameters>();Common(P,V,Color,Mobile,Capture);
        P->ReconInput=Rec;P->ReconCin=Color.Texture;P->RenderTargets[0]=Output.GetRenderTargetBinding();
        TShaderMapRef<FReconCompositePS> Composite(GetGlobalShaderMap(V.GetFeatureLevel()));
        AddDrawScreenPass(Graph,RDG_EVENT_NAME("UEFog.Recon.ComposeActualST"),V,FScreenPassTextureViewport(Output),FScreenPassTextureViewport(Color),Composite,P);
        State->Current.Composite=1;UEFogMobilePerformanceBoundary(Graph,4);
        State->Current.Low=FScreenPassTexture(Low,FIntRect(FIntPoint::ZeroValue,F));State->Current.Guide=FScreenPassTexture(Guide,FIntRect(FIntPoint::ZeroValue,F));
        State->Current.Reconstructed=FScreenPassTexture(Rec,FIntRect(FIntPoint::ZeroValue,Size));State->Current.Returned=Output;State->Current.Aux=Aux;
        if(Capture)
        {
            State->Record->SetArrayField(TEXT("allocatedFogSize"),Numbers({double(F.X),double(F.Y)}));
            State->Record->SetArrayField(TEXT("allocatedReconstructionSize"),Numbers({double(Size.X),double(Size.Y)}));
            State->Record->SetObjectField(TEXT("lowST"),Desc(Low));State->Record->SetObjectField(TEXT("lowGuide"),Desc(Guide));
            State->Record->SetObjectField(TEXT("reconstructedST"),Desc(Rec));State->Record->SetObjectField(TEXT("returnedOutput"),Desc(Output.Texture));
            State->Record->SetBoolField(TEXT("fullIdentityResource"),Rec==Low);State->Record->SetBoolField(TEXT("overrideOutputActive"),Inputs.OverrideOutput.IsValid());
            State->Record->SetNumberField(TEXT("integrateDispatches"),1);State->Record->SetNumberField(TEXT("reconstructDispatches"),State->Code==1?0:1);
            State->Record->SetNumberField(TEXT("compositePasses"),1);Save();
        }
        return Output;
    }
    FScreenPassTexture Observe(FRDGBuilder& Graph,const FSceneView& V,const FPostProcessMaterialInputs& Inputs)
    {
        ++State->ObserverCallbacks;const auto Mobile=Inputs.SceneTextures.MobileSceneTextures.GetUniformBuffer();
        const auto Actual=FScreenPassTexture::CopyFromSlice(Graph,Inputs.GetInput(EPostProcessMaterialInput::SceneColor));
        if(!Mobile||!Actual.IsValid())return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        if(IsUEFogMobilePerformanceActive())
        {
            const bool Same=State->Current.Number==V.Family->FrameNumber&&(State->Code==0?State->Current.Cin==Actual:State->Current.Returned==Actual)&&!State->Diagnostics&&!State->Readback&&!State->Scheduled;
            TSharedPtr<FJsonObject> J;
            if(UEFogMobilePerformanceNeedsSnapshot())
            {
                J=Snapshot(V,State->Current.Cin,Mobile);J->SetBoolField(TEXT("diagnosticsEnabled"),State->Diagnostics);
                J->SetNumberField(TEXT("auxElements"),State->Code==0?0:1);
                J->SetNumberField(TEXT("integrateDispatches"),State->Current.Integrate);J->SetNumberField(TEXT("reconstructDispatches"),State->Current.Reconstruct);J->SetNumberField(TEXT("compositePasses"),State->Current.Composite);
                if(State->Code!=0&&State->Current.Low.IsValid())
                {
                    const auto F=State->Current.Low.ViewRect.Size();
                    J->SetArrayField(TEXT("allocatedFogSize"),Numbers({double(F.X),double(F.Y)}));
                    J->SetArrayField(TEXT("allocatedReconstructionSize"),Numbers({1552,720}));
                    J->SetObjectField(TEXT("lowST"),Desc(State->Current.Low.Texture));J->SetObjectField(TEXT("lowGuide"),Desc(State->Current.Guide.Texture));
                    J->SetObjectField(TEXT("reconstructedST"),Desc(State->Current.Reconstructed.Texture));J->SetObjectField(TEXT("returnedOutput"),Desc(State->Current.Returned.Texture));
                }
                J->SetBoolField(TEXT("overrideOutputActive"),Inputs.OverrideOutput.IsValid());
                J->SetObjectField(TEXT("observerInput"),Desc(Actual.Texture));J->SetBoolField(TEXT("returnedResourceEqualsObserverInput"),Same);
            }
            UEFogMobilePerformanceWitness(J,Same,State->Current.Integrate,State->Current.Reconstruct,State->Current.Composite);
            return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        }
        if(State->Readback&&!State->Finished&&State->Readback->IsReady())
        {
            TArray<uint8> Bytes;Bytes.SetNumUninitialized(State->Bytes);
            if(const void* Raw=State->Readback->Lock(State->Bytes))
            {
                FMemory::Memcpy(Bytes.GetData(),Raw,State->Bytes);State->Readback->Unlock();
                const bool OK=FFileHelper::SaveArrayToFile(Bytes,*FPaths::Combine(State->Directory,TEXT("reconstruction-buffer.bin")));
                State->Record->SetBoolField(TEXT("gpuCompletionConfirmed"),OK);State->Record->SetStringField(TEXT("status"),OK?TEXT("complete"):TEXT("failed"));
                State->Record->SetNumberField(TEXT("completionFrame"),V.Family->FrameNumber);State->Record->SetNumberField(TEXT("bufferBytes"),State->Bytes);
                State->Record->SetNumberField(TEXT("bufferStride"),16);State->Record->SetStringField(TEXT("dtype"),TEXT("<f4"));
                State->Record->SetStringField(TEXT("rowPitch"),TEXT("not applicable; tightly packed structured buffer"));
                State->Finished=true;State->Readback.Reset();Save();
            }
        }
        if(State->Code==0&&State->ObserverCallbacks>=180&&State->ObserverCallbacks%120==60)
        {State->Record=Snapshot(V,Actual,Mobile);State->Record->SetStringField(TEXT("status"),TEXT("control-complete"));Save();}
        if(State->Diagnostics&&State->ProducerCallbacks==180&&!State->Scheduled&&State->Current.Probe)
        {
            const bool SameFrame=State->Current.Number==V.Family->FrameNumber;
            const bool Same=SameFrame&&(State->Code==4||State->Current.Returned==Actual);
            State->Record->SetBoolField(TEXT("returnedResourceEqualsObserverInput"),Same);State->Record->SetNumberField(TEXT("observerFrame"),V.Family->FrameNumber);
            State->Record->SetObjectField(TEXT("observerInput"),Desc(Actual.Texture));
            if(!Same){State->Record->SetStringField(TEXT("status"),TEXT("failed-consumer-resource"));Save();return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);}
            const FIntPoint Size=Actual.ViewRect.Size(),F=FogSize(Size);
            const uint64 Rows=State->Code==4?4ull:4ull+2ull*F.X*F.Y+4ull*Size.X*Size.Y;
            if(Rows*16>=134217728ull){State->Record->SetStringField(TEXT("status"),TEXT("dense-buffer-budget-failed"));Save();return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);}
            auto* Buffer=Graph.CreateBuffer(FRDGBufferDesc::CreateStructuredDesc(sizeof(FVector4f),uint32(Rows)),TEXT("UEFog.Recon.DenseActualSamples"));
            if(State->Code==4)
            {
                auto* P=Graph.AllocParameters<FReconKnownObserveCS::FParameters>();Common(P,V,Actual,Mobile,true);
                P->ReconProbeInput=State->Current.Probe;P->ReconSamples=Graph.CreateUAV(Buffer);
                TShaderMapRef<FReconKnownObserveCS> Shader(GetGlobalShaderMap(V.GetFeatureLevel()));
                FComputeShaderUtils::AddPass(Graph,RDG_EVENT_NAME("UEFog.Recon.R32ActualRead"),Shader,P,FIntVector(1,1,1));
            }
            else
            {
                auto* P=Graph.AllocParameters<FReconDenseObserveCS::FParameters>();Common(P,V,Actual,Mobile,true);
                P->ReconProbeInput=State->Current.Probe;P->ReconLowInput=State->Current.Low.Texture;P->ReconGuideInput=State->Current.Guide.Texture;
                P->ReconInput=State->Current.Reconstructed.Texture;P->ReconAuxInput=Graph.CreateSRV(State->Current.Aux);
                P->ReconCin=State->Current.Cin.Texture;P->ReconConsumed=Actual.Texture;P->ReconSamples=Graph.CreateUAV(Buffer);
                TShaderMapRef<FReconDenseObserveCS> Shader(GetGlobalShaderMap(V.GetFeatureLevel()));
                FComputeShaderUtils::AddPass(Graph,RDG_EVENT_NAME("UEFog.Recon.ObserveEngineConsumerDense"),Shader,P,FComputeShaderUtils::GetGroupCount(Size,FIntPoint(8,8)));
            }
            State->Bytes=uint32(Rows*16);State->Readback=MakeUnique<FRHIGPUBufferReadback>(TEXT("UEFog.Recon.DenseCopy"));
            AddEnqueueCopyPass(Graph,State->Readback.Get(),Buffer,State->Bytes);State->Scheduled=true;
            State->Record->SetNumberField(TEXT("sampleDispatches"),1);State->Record->SetNumberField(TEXT("probeReadDispatches"),1);
            State->Record->SetNumberField(TEXT("readbackRequests"),1);Save();
        }
        return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
    }
};
}
#endif

bool ApplyUEFogMobileReconstructionLaunch(FString& Error)
{
    FString Profile;if(!FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileRecon="),Profile))return true;
#if PLATFORM_ANDROID
    using namespace MobileRecon;FString Other;
    const TArray<FString> Profiles={TEXT("Off"),TEXT("Full64"),TEXT("HalfB64"),TEXT("HalfG64"),TEXT("GuideProbe")};
    const int32 Index=Profiles.IndexOfByKey(Profile);
    if(Index==INDEX_NONE||FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileFog="),Other)||FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileProbe="),Other)||IsUEFogCompareActive())
    {Error=TEXT("Recon requires its isolated profile with no old Fog/PRE/Compare entry");return false;}
    State=MakeUnique<FState>();State->Profile=Profile;State->Code=uint32(Index);State->Grid=TEXT("Half");State->FixtureName=TEXT("Planes");
    FParse::Value(FCommandLine::Get(),TEXT("FogLabReconGrid="),State->Grid);FParse::Value(FCommandLine::Get(),TEXT("FogLabReconFixture="),State->FixtureName);
    FParse::Value(FCommandLine::Get(),TEXT("FogLabReconNear="),State->Near);FParse::Value(FCommandLine::Get(),TEXT("FogLabReconSigma="),State->Sigma);
    int32 Diagnostic=0;FParse::Value(FCommandLine::Get(),TEXT("FogLabReconReadback="),Diagnostic);
    if((State->Grid!=TEXT("Half")&&State->Grid!=TEXT("Odd"))||(State->FixtureName!=TEXT("Planes")&&State->FixtureName!=TEXT("ThinStripe"))||
       (State->Near!=400&&State->Near!=600)||(State->Sigma!=0&&State->Sigma!=.35&&State->Sigma!=.70)||(Diagnostic!=0&&Diagnostic!=1))
    {Error=TEXT("Unfrozen reconstruction argument");State.Reset();return false;}
    State->Diagnostics=State->Code!=0&&(Diagnostic==1||State->Code==4);
    FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileRun="),State->RunId);FParse::Value(FCommandLine::Get(),TEXT("FogLabMobileOutput="),State->Directory);
    if(IsUEFogMobilePerformanceActive())
    {FParse::Value(FCommandLine::Get(),TEXT("FogLabMobilePerf="),State->RunId);FParse::Value(FCommandLine::Get(),TEXT("FogLabPerfOutput="),State->Directory);}
    FParse::Value(FCommandLine::Get(),TEXT("FogLabBaselineBuild="),State->BuildId);FParse::Value(FCommandLine::Get(),TEXT("FogLabBaselineSource="),State->Source);
    if(State->Diagnostics&&(State->RunId.Len()!=32||State->Directory.IsEmpty()||State->BuildId.IsEmpty()||State->Source.IsEmpty()))
    {Error=TEXT("Diagnostic reconstruction needs private UUID/package source witness");State.Reset();return false;}
    const uint32 Tag=FCString::Strtoui64(*State->RunId.Left(5),nullptr,16);State->TagA=Tag&1023;State->TagB=(Tag>>10)&1023;
    IConsoleManager::Get().FindConsoleVariable(TEXT("r.Mobile.ForceDepthResolve"))->Set(1,ECVF_SetByCommandline);return true;
#else
    Error=TEXT("Mobile reconstruction is Android-only");return false;
#endif
}
TSharedPtr<ISceneViewExtension,ESPMode::ThreadSafe> CreateUEFogMobileReconstructionExtension()
{
#if PLATFORM_ANDROID
    if(MobileRecon::State)return FSceneViewExtensions::NewExtension<MobileRecon::FExtension>();
#endif
    return nullptr;
}
void UEFogMobileReconstructionFixtureReady(UWorld* World,TSharedRef<FJsonObject> Fixture)
{
#if PLATFORM_ANDROID
    if(MobileRecon::State){MobileRecon::State->Scene=World->Scene;MobileRecon::State->Fixture=Fixture;MobileRecon::State->Ready.store(true,std::memory_order_release);}
#endif
}
void ShutdownUEFogMobileReconstruction()
{
#if PLATFORM_ANDROID
    MobileRecon::State.Reset();
#endif
}
