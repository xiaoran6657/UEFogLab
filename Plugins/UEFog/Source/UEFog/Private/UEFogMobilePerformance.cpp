#include "UEFogMobilePerformance.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

#if PLATFORM_ANDROID
#include "IVulkanDynamicRHI.h"
#include "RenderGraphBuilder.h"
#include "SceneView.h"
#include "RHICommandList.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Misc/App.h"
#include "Misc/CoreDelegates.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "Serialization/JsonSerializer.h"
#include "UnrealClient.h"
#include <atomic>
#include <cstdio>
#include <unistd.h>

namespace MobilePerf
{
constexpr uint32 QueriesPerFrame=6, MaxFrames=8192;
struct FSample
{
    uint32 Frame=0, Slot=0, ReadyFrame=0, Attempts=0, Integrate=0, Reconstruct=0, Composite=0;
    double CPUSeconds=0;
    std::atomic<uint32> Issued{0};
    uint64 Ticks[QueriesPerFrame]={}, Availability[QueriesPerFrame]={};
    int32 Result=VK_NOT_READY;
    bool Ready=false, Witness=false, SameResource=false, Focus=false, Screenshot=false;
};
struct FNative
{
    IVulkanDynamicRHI* RHI=nullptr;
    VkDevice Device=VK_NULL_HANDLE;
    VkQueryPool Pool=VK_NULL_HANDLE;
    PFN_vkCmdResetQueryPool Reset=nullptr;
    PFN_vkCmdWriteTimestamp Timestamp=nullptr;
    PFN_vkGetQueryPoolResults Results=nullptr;
    PFN_vkDestroyQueryPool Destroy=nullptr;
    VkPhysicalDeviceProperties Props{};
    uint32 Family=0, Index=0, ValidBits=0;
    FGPUFenceRHIRef InitializationFence;
    std::atomic<bool> ResetPassRecorded{false};
    std::atomic<uint32> ResetCommandsRecorded{0};
    uint32 ResetFrame=0, ReadyFrame=0, FencePolls=0, FenceNotReady=0, QueryPollingSkippedUntilInitialized=0;
    bool Initialized=false, PreIssueFenceReady=false;
};
enum class EPhase { Warmup, InitializePool, AwaitArm, Settle, Sample, Drain, Done };
struct FState
{
    FString RunId, Directory, BuildId, Source, Profile, Error;
    uint64 StartTicks=0;
    double Start=0, Armed=0, SampleStart=0, SampleEnd=0, DrainStart=0;
    uint32 WarmFrames=0, LastFrame=0, ArmFrame=0, StartFrame=0, EndFrame=0;
    EPhase Phase=EPhase::Warmup;
    std::atomic<bool> FocusLost{false};
    FDelegateHandle Deactivate, Background;
    TSharedPtr<FNative,ESPMode::ThreadSafe> Native;
    TArray<TSharedPtr<FSample,ESPMode::ThreadSafe>> Samples;
    TSharedPtr<FSample,ESPMode::ThreadSafe> Current;
    TSharedPtr<FJsonObject> Snapshot;
    TArray<TSharedPtr<FJsonValue>> Bookmarks;
};
TUniquePtr<FState> State;
TArray<TSharedPtr<FJsonValue>> N(std::initializer_list<double> Values)
{ TArray<TSharedPtr<FJsonValue>> A;for(double X:Values)A.Add(MakeShared<FJsonValueNumber>(X));return A; }
void Write(const TCHAR* Name,TSharedRef<FJsonObject> Object)
{
    FString Text;FJsonSerializer::Serialize(Object,TJsonWriterFactory<>::Create(&Text));
    if(!FFileHelper::SaveStringToFile(Text,*FPaths::Combine(State->Directory,Name),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
        State->Error=TEXT("private metadata write failed");
}
void Bookmark(const TCHAR* Name,uint32 Frame,double Time)
{
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("name"),Name);J->SetNumberField(TEXT("familyFrame"),Frame);
    J->SetNumberField(TEXT("cpuMonotonicSeconds"),Time);State->Bookmarks.Add(MakeShared<FJsonValueObject>(J));
}
TSharedRef<FJsonObject> Identity()
{
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("kind"),TEXT("UEFogLab.MobilePerformance"));J->SetNumberField(TEXT("schemaVersion"),2);
    J->SetStringField(TEXT("runId"),State->RunId);J->SetStringField(TEXT("buildId"),State->BuildId);J->SetStringField(TEXT("sourceRevision"),State->Source);
    J->SetStringField(TEXT("profile"),State->Profile);J->SetNumberField(TEXT("pid"),FPlatformProcess::GetCurrentProcessId());
    J->SetStringField(TEXT("processStartTicks"),LexToString(State->StartTicks));J->SetStringField(TEXT("engineVersion"),FEngineVersion::Current().ToString());
    J->SetStringField(TEXT("commandLine"),FCommandLine::Get());
    if(const auto V=State->Native)
    {
        auto I=MakeShared<FJsonObject>();I->SetBoolField(TEXT("gpuResetComplete"),V->Initialized);
        I->SetBoolField(TEXT("resetPassRecorded"),V->ResetPassRecorded.load(std::memory_order_acquire));
        I->SetNumberField(TEXT("resetCommandsRecorded"),V->ResetCommandsRecorded.load(std::memory_order_acquire));
        I->SetNumberField(TEXT("resetFamilyFrame"),V->ResetFrame);I->SetNumberField(TEXT("readyFamilyFrame"),V->ReadyFrame);
        I->SetNumberField(TEXT("fencePolls"),V->FencePolls);I->SetNumberField(TEXT("fenceNotReadyObservations"),V->FenceNotReady);
        I->SetNumberField(TEXT("queryPollingSkippedUntilInitialized"),V->QueryPollingSkippedUntilInitialized);
        I->SetBoolField(TEXT("preIssueFenceReady"),V->PreIssueFenceReady);I->SetNumberField(TEXT("initializedQueryCount"),MaxFrames*QueriesPerFrame);
        I->SetBoolField(TEXT("slotReuse"),false);I->SetStringField(TEXT("method"),TEXT("whole pool vkCmdResetQueryPool -> ordered RHI GPU fence -> nonblocking Poll ready before ready/arm; unique slots, no per-frame reset"));
        J->SetObjectField(TEXT("queryInitialization"),I);
    }
    return J;
}
bool Init()
{
    if(State->Native)return true;
    if(!GDynamicRHI||GDynamicRHI->GetInterfaceType()!=ERHIInterfaceType::Vulkan){State->Error=TEXT("not Vulkan");return false;}
    auto V=MakeShared<FNative,ESPMode::ThreadSafe>();V->RHI=GetIVulkanDynamicRHI();V->Device=V->RHI->RHIGetVkDevice();
    auto Props=reinterpret_cast<PFN_vkGetPhysicalDeviceProperties>(V->RHI->RHIGetVkInstanceProcAddr("vkGetPhysicalDeviceProperties"));
    auto Queues=reinterpret_cast<PFN_vkGetPhysicalDeviceQueueFamilyProperties>(V->RHI->RHIGetVkInstanceProcAddr("vkGetPhysicalDeviceQueueFamilyProperties"));
    auto Create=reinterpret_cast<PFN_vkCreateQueryPool>(V->RHI->RHIGetVkDeviceProcAddr("vkCreateQueryPool"));
    V->Reset=reinterpret_cast<PFN_vkCmdResetQueryPool>(V->RHI->RHIGetVkDeviceProcAddr("vkCmdResetQueryPool"));
    V->Timestamp=reinterpret_cast<PFN_vkCmdWriteTimestamp>(V->RHI->RHIGetVkDeviceProcAddr("vkCmdWriteTimestamp"));
    V->Results=reinterpret_cast<PFN_vkGetQueryPoolResults>(V->RHI->RHIGetVkDeviceProcAddr("vkGetQueryPoolResults"));
    V->Destroy=reinterpret_cast<PFN_vkDestroyQueryPool>(V->RHI->RHIGetVkDeviceProcAddr("vkDestroyQueryPool"));
    if(!Props||!Queues||!Create||!V->Reset||!V->Timestamp||!V->Results||!V->Destroy){State->Error=TEXT("missing public Vulkan function");return false;}
    Props(V->RHI->RHIGetVkPhysicalDevice(),&V->Props);
    V->Family=V->RHI->RHIGetGraphicsQueueFamilyIndex();V->Index=V->RHI->RHIGetGraphicsQueueIndex();
    uint32 Count=0;Queues(V->RHI->RHIGetVkPhysicalDevice(),&Count,nullptr);TArray<VkQueueFamilyProperties> Q;Q.SetNum(Count);
    Queues(V->RHI->RHIGetVkPhysicalDevice(),&Count,Q.GetData());
    if(V->Family>=Count||!(Q[V->Family].queueFlags&VK_QUEUE_GRAPHICS_BIT)||!V->Props.limits.timestampComputeAndGraphics||V->Props.limits.timestampPeriod<=0||Q[V->Family].timestampValidBits==0)
    {State->Error=TEXT("unsupported graphics timestamp capability");return false;}
    V->ValidBits=Q[V->Family].timestampValidBits;
    VkQueryPoolCreateInfo Info{};Info.sType=VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO;Info.queryType=VK_QUERY_TYPE_TIMESTAMP;Info.queryCount=MaxFrames*QueriesPerFrame;
    const VkResult Result=Create(V->Device,&Info,nullptr,&V->Pool);
    if(Result!=VK_SUCCESS){State->Error=FString::Printf(TEXT("vkCreateQueryPool=%d"),int32(Result));return false;}
    V->InitializationFence=RHICreateGPUFence(TEXT("UEFog.Perf.QueryPoolInitialized"));
    State->Native=V;return true;
}
void InitializePool(FRDGBuilder& Graph,uint32 Frame)
{
    const auto V=State->Native;V->ResetFrame=Frame;
    // Real non-ready control before this new fence has ever been issued. This
    // never reads an uninitialized query and does not delay the GPU.
    V->PreIssueFenceReady=V->InitializationFence->Poll();++V->FencePolls;
    if(!V->PreIssueFenceReady)++V->FenceNotReady;
    Graph.AddPass(RDG_EVENT_NAME("UEFog.Perf.InitializeQueryPool"),ERDGPassFlags::None,
        [V](FRHICommandList& List)
        {
            List.EnqueueLambda([V](FRHICommandListBase& Executing)
            {
                check(Executing.GetPipeline()==ERHIPipeline::Graphics);
                V->Reset(V->RHI->RHIGetVkCommandBuffer(Executing),V->Pool,0,MaxFrames*QueriesPerFrame);
                V->ResetCommandsRecorded.fetch_add(1,std::memory_order_release);
            });
            // Use the public top-of-pipe wrapper, not context.RHIWriteGPUFence.
            // Poll guarantees GPU completion, including Context sync points on
            // minimal-submit Vulkan. No Wait() and no per-frame fence/reset.
            List.WriteGPUFence(V->InitializationFence);
            V->ResetPassRecorded.store(true,std::memory_order_release);
        });
}
void Poll(uint32 Frame)
{
    if(!State->Native)return;
    if(!State->Native->Initialized){++State->Native->QueryPollingSkippedUntilInitialized;return;}
    const auto V=State->Native;
    for(auto& S:State->Samples)
    {
        if(S->Ready||S->Issued.load(std::memory_order_acquire)!=63||
           (State->Phase!=EPhase::Drain&&Frame-S->Frame<8))continue;
        struct FResult {uint64 Tick,Available;};FResult Results[QueriesPerFrame]{};
        ++S->Attempts;
        S->Result=V->Results(V->Device,V->Pool,S->Slot*QueriesPerFrame,QueriesPerFrame,sizeof(Results),Results,sizeof(FResult),VK_QUERY_RESULT_64_BIT|VK_QUERY_RESULT_WITH_AVAILABILITY_BIT);
        bool Ready=S->Result==VK_SUCCESS;
        for(uint32 I=0;I<QueriesPerFrame;++I){S->Availability[I]=Results[I].Available;Ready=Ready&&Results[I].Available!=0;}
        if(Ready){for(uint32 I=0;I<QueriesPerFrame;++I)S->Ticks[I]=Results[I].Tick;S->Ready=true;S->ReadyFrame=Frame;}
        else if(S->Result!=VK_NOT_READY&&S->Result!=VK_SUCCESS)State->Error=TEXT("vkGetQueryPoolResults failed");
    }
}
void Finish(uint32 Frame,double Now)
{
    auto J=Identity();bool Complete=State->Error.IsEmpty()&&State->Samples.Num()>=120&&!State->FocusLost.load();
    auto Clock=MakeShared<FJsonObject>();
    if(State->Native)
    {
        const auto V=State->Native;
        Clock->SetStringField(TEXT("kind"),TEXT("VulkanDeviceTimestamp"));Clock->SetStringField(TEXT("unit"),TEXT("device ticks"));
        Clock->SetNumberField(TEXT("timestampPeriodNs"),V->Props.limits.timestampPeriod);Clock->SetNumberField(TEXT("timestampValidBits"),V->ValidBits);
        Clock->SetBoolField(TEXT("timestampComputeAndGraphics"),V->Props.limits.timestampComputeAndGraphics!=0);
        Clock->SetStringField(TEXT("deviceName"),UTF8_TO_TCHAR(V->Props.deviceName));Clock->SetNumberField(TEXT("driverVersion"),V->Props.driverVersion);
        Clock->SetNumberField(TEXT("queueFamily"),V->Family);Clock->SetNumberField(TEXT("queueIndex"),V->Index);
    }
    Clock->SetStringField(TEXT("queue"),TEXT("Graphics"));Clock->SetStringField(TEXT("stage"),TEXT("VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT"));
    Clock->SetStringField(TEXT("commandBufferAPI"),TEXT("IVulkanDynamicRHI::RHIGetVkCommandBuffer(executing graphics RHI command list)"));
    Clock->SetStringField(TEXT("conversion"),TEXT("((end-start) & ((1<<validBits)-1)) * timestampPeriodNs / 1e6 = ms; reject >= half-wrap"));
    Clock->SetBoolField(TEXT("waitFlag"),false);Clock->SetNumberField(TEXT("queriesPerFrame"),QueriesPerFrame);J->SetObjectField(TEXT("clock"),Clock);
    J->SetNumberField(TEXT("warmupSeconds"),State->Armed-State->Start);J->SetNumberField(TEXT("warmupFrames"),State->WarmFrames);
    J->SetNumberField(TEXT("armedSeconds"),State->Armed);J->SetNumberField(TEXT("sampleStartSeconds"),State->SampleStart);
    J->SetNumberField(TEXT("armedFamilyFrame"),State->ArmFrame);
    J->SetNumberField(TEXT("sampleEndSeconds"),State->SampleEnd);J->SetNumberField(TEXT("sampleStartFrame"),State->StartFrame);
    J->SetNumberField(TEXT("sampleEndFrameExclusive"),State->EndFrame);J->SetNumberField(TEXT("completedFamilyFrame"),Frame);
    J->SetNumberField(TEXT("completionSeconds"),Now);J->SetNumberField(TEXT("requestedSampleSeconds"),8);J->SetNumberField(TEXT("settleSeconds"),2);
    J->SetBoolField(TEXT("focusLost"),State->FocusLost.load());J->SetBoolField(TEXT("hasFocusAtCompletion"),FApp::HasFocus());
    J->SetStringField(TEXT("error"),State->Error);J->SetArrayField(TEXT("bookmarks"),State->Bookmarks);
    if(State->Snapshot)J->SetObjectField(TEXT("rendering"),State->Snapshot.ToSharedRef());else Complete=false;
    auto Config=MakeShared<FJsonObject>();
    for(const TCHAR* Key:{TEXT("r.VSync"),TEXT("t.MaxFPS"),TEXT("r.ScreenPercentage"),TEXT("r.SecondaryScreenPercentage.GameViewport"),TEXT("r.DynamicRes.OperationMode"),TEXT("r.SetFramePace"),TEXT("a.UseSwappyForFramePacing"),TEXT("rhi.SyncInterval"),TEXT("r.GPUStatsEnabled"),TEXT("r.RHIThread.Enable"),TEXT("r.Vulkan.EnableValidation"),TEXT("r.Vulkan.GPUValidation")})
    {const auto* C=IConsoleManager::Get().FindConsoleVariable(Key);if(C)Config->SetStringField(Key,C->GetString());else Config->SetStringField(Key,TEXT("unavailable"));}
    J->SetObjectField(TEXT("timingCvars"),Config);
    TArray<TSharedPtr<FJsonValue>> Rows;
    for(const auto& S:State->Samples)
    {
        auto R=MakeShared<FJsonObject>();R->SetNumberField(TEXT("familyFrame"),S->Frame);R->SetNumberField(TEXT("querySlot"),S->Slot);
        R->SetStringField(TEXT("phase"),TEXT("sample"));R->SetNumberField(TEXT("cpuMonotonicSeconds"),S->CPUSeconds);
        R->SetNumberField(TEXT("issuedMask"),S->Issued.load());R->SetBoolField(TEXT("ready"),S->Ready);R->SetNumberField(TEXT("readyFamilyFrame"),S->ReadyFrame);
        R->SetNumberField(TEXT("queryAttempts"),S->Attempts);R->SetNumberField(TEXT("vkResult"),S->Result);
        R->SetBoolField(TEXT("hasFocus"),S->Focus);R->SetBoolField(TEXT("screenshotRequested"),S->Screenshot);
        R->SetBoolField(TEXT("normalWitness"),S->Witness);R->SetBoolField(TEXT("sameConsumerResource"),S->SameResource);
        R->SetArrayField(TEXT("dispatches"),N({double(S->Integrate),double(S->Reconstruct),double(S->Composite)}));
        TArray<TSharedPtr<FJsonValue>> T,A;for(uint32 I=0;I<QueriesPerFrame;++I){T.Add(MakeShared<FJsonValueString>(LexToString(S->Ticks[I])));A.Add(MakeShared<FJsonValueNumber>(double(S->Availability[I])));}
        R->SetArrayField(TEXT("ticks"),T);R->SetArrayField(TEXT("availability"),A);Rows.Add(MakeShared<FJsonValueObject>(R));
        Complete=Complete&&S->Ready&&S->Witness&&S->SameResource&&S->Focus&&!S->Screenshot;
    }
    J->SetArrayField(TEXT("samples"),Rows);J->SetBoolField(TEXT("completed"),Complete);
    J->SetStringField(TEXT("status"),Complete?TEXT("complete"):TEXT("invalid"));
    J->SetStringField(TEXT("frameScope"),TEXT("ViewFamilyGraph: PreRenderViewFamily to PostRenderViewFamily on graphics queue; excludes present/pacing and work outside callbacks; not whole GPU frame"));
    J->SetBoolField(TEXT("startupWitnessActive"),false);J->SetBoolField(TEXT("diagnosticsEnabled"),false);
    J->SetNumberField(TEXT("fogPayloadReadbacks"),0);J->SetNumberField(TEXT("screenshotRequests"),0);J->SetNumberField(TEXT("perFrameFileWrites"),0);
    Bookmark(TEXT("complete"),Frame,Now);J->SetArrayField(TEXT("bookmarks"),State->Bookmarks);Write(TEXT("performance.json"),J);State->Phase=EPhase::Done;
}
}
#endif

bool ApplyUEFogMobilePerformanceLaunch(FString& Error)
{
    FString Id;if(!FParse::Value(FCommandLine::Get(),TEXT("FogLabMobilePerf="),Id))return true;
#if PLATFORM_ANDROID
    using namespace MobilePerf;
    const FString Cmd=FCommandLine::Get();
    for(const TCHAR* Forbidden:{TEXT("FogLabMobileRun="),TEXT("FogLabMobileFog="),TEXT("FogLabMobileProbe="),TEXT("FogLabCompareMode="),TEXT("FogLabCapture="),TEXT("FogLabReadback="),TEXT("trace="),TEXT("tracefile="),TEXT("renderdoc"),TEXT("vulkandebug"),TEXT("gpuvalidation"),TEXT("d3ddebug")})
        if(Cmd.Contains(Forbidden,ESearchCase::IgnoreCase)){Error=TEXT("Performance cannot mix startup/capture/diagnostic/trace/debug entry");return false;}
    auto S=MakeUnique<FState>();S->RunId=Id;int32 Readback=-1;FString Grid,Fixture,Near,Sigma,Exposure;
    FParse::Value(*Cmd,TEXT("FogLabMobileRecon="),S->Profile);FParse::Value(*Cmd,TEXT("FogLabReconReadback="),Readback);
    FParse::Value(*Cmd,TEXT("FogLabReconGrid="),Grid);FParse::Value(*Cmd,TEXT("FogLabReconFixture="),Fixture);
    FParse::Value(*Cmd,TEXT("FogLabReconNear="),Near);FParse::Value(*Cmd,TEXT("FogLabReconSigma="),Sigma);FParse::Value(*Cmd,TEXT("FogLabPreExposure="),Exposure);
    FParse::Value(*Cmd,TEXT("FogLabPerfOutput="),S->Directory);FParse::Value(*Cmd,TEXT("FogLabBaselineBuild="),S->BuildId);FParse::Value(*Cmd,TEXT("FogLabBaselineSource="),S->Source);
    bool Hex=Id.Len()==32;for(TCHAR C:Id)Hex=Hex&&FChar::IsHexDigit(C);
    if(!Hex||!(S->Profile==TEXT("Off")||S->Profile==TEXT("Full64")||S->Profile==TEXT("HalfB64")||S->Profile==TEXT("HalfG64"))||
       Readback!=0||Grid!=TEXT("Half")||Fixture!=TEXT("Planes")||Near!=TEXT("600")||FCString::Atod(*Sigma)!=.35||Exposure!=TEXT("1")||
       S->BuildId.IsEmpty()||S->Source.Len()!=40||!S->Directory.StartsWith(TEXT("/data/user/"))||S->Directory.Contains(TEXT(".."))||
       !S->Directory.EndsWith(TEXT("/com.uefoglab.mobilebaseline/files/M4-PERF/")+Id))
    {Error=TEXT("Performance requires fixed normal four-mode configuration and private UUID/package identity");return false;}
    if(FILE* F=fopen("/proc/self/stat","r"))
    {char B[2048]={};if(fgets(B,sizeof(B),F)){FString T=UTF8_TO_TCHAR(B);int32 End=0;if(T.FindLastChar(TEXT(')'),End)){TArray<FString>A;T.Mid(End+2).ParseIntoArrayWS(A);if(A.Num()>19)S->StartTicks=FCString::Strtoui64(*A[19],nullptr,10);}}fclose(F);}
    IFileManager::Get().MakeDirectory(*S->Directory,true);State=MoveTemp(S);
    State->Deactivate=FCoreDelegates::ApplicationWillDeactivateDelegate.AddLambda([]{if(State)State->FocusLost.store(true);});
    State->Background=FCoreDelegates::ApplicationWillEnterBackgroundDelegate.AddLambda([]{if(State)State->FocusLost.store(true);});
    return true;
#else
    Error=TEXT("Mobile performance is Android-only");return false;
#endif
}
bool IsUEFogMobilePerformanceActive()
{
#if PLATFORM_ANDROID
    return MobilePerf::State!=nullptr;
#else
    return false;
#endif
}
void UEFogMobilePerformanceBegin(FRDGBuilder& Graph,const FSceneView& View)
{
#if PLATFORM_ANDROID
    using namespace MobilePerf;if(!State||State->Phase==EPhase::Done)return;
    State->Current.Reset();const double Now=FPlatformTime::Seconds();const uint32 Frame=View.Family->FrameNumber;State->LastFrame=Frame;
    if(!State->Start){State->Start=Now;Bookmark(TEXT("warmup-start"),Frame,Now);}
    Poll(Frame);
    if(State->Phase==EPhase::Warmup)
    {
        ++State->WarmFrames;if(Now-State->Start<60||State->WarmFrames<180)return;
        if(!Init()){Finish(Frame,Now);return;}
        InitializePool(Graph,Frame);State->Phase=EPhase::InitializePool;return;
    }
    if(State->Phase==EPhase::InitializePool)
    {
        const auto V=State->Native;++V->FencePolls;
        if(!V->ResetPassRecorded.load(std::memory_order_acquire)||V->InitializationFence->NumPendingWriteCommands.GetValue()!=0||!V->InitializationFence->Poll())
        {++V->FenceNotReady;return;}
        V->ReadyFrame=Frame;V->Initialized=true;
        State->Phase=EPhase::AwaitArm;Bookmark(TEXT("warmup-ready"),Frame,Now);
        auto J=Identity();J->SetStringField(TEXT("status"),TEXT("await-arm"));J->SetNumberField(TEXT("warmupFrames"),State->WarmFrames);J->SetNumberField(TEXT("warmupSeconds"),Now-State->Start);Write(TEXT("ready.json"),J);
    }
    if(State->Phase==EPhase::AwaitArm)
    {
        FString Arm;if(!FFileHelper::LoadFileToString(Arm,*FPaths::Combine(State->Directory,TEXT("arm.txt")))||Arm.TrimStartAndEnd()!=State->RunId)return;
        check(State->Native->Initialized);
        State->Armed=Now;State->ArmFrame=Frame;State->Phase=EPhase::Settle;State->FocusLost.store(false);Bookmark(TEXT("arm"),Frame,Now);
    }
    if(State->Phase==EPhase::Settle)
    {if(Now-State->Armed<2)return;State->SampleStart=Now;State->StartFrame=Frame;State->Phase=EPhase::Sample;Bookmark(TEXT("sample-start"),Frame,Now);}
    if(State->Phase==EPhase::Sample&&Now-State->SampleStart>=8)
    {State->SampleEnd=Now;State->EndFrame=Frame;State->DrainStart=Now;State->Phase=EPhase::Drain;Bookmark(TEXT("sample-end"),Frame,Now);}
    if(State->Phase==EPhase::Drain)
    {
        bool Ready=true;for(auto& S:State->Samples)Ready=Ready&&S->Ready;
        if(Ready||Now-State->DrainStart>20)Finish(Frame,Now);return;
    }
    if(State->Phase!=EPhase::Sample)return;
    if(State->Samples.Num()>=int32(MaxFrames)){State->Error=TEXT("query capacity exceeded");State->SampleEnd=Now;Finish(Frame,Now);return;}
    auto S=MakeShared<FSample,ESPMode::ThreadSafe>();S->Frame=Frame;S->Slot=State->Samples.Num();S->CPUSeconds=Now;S->Focus=FApp::HasFocus();S->Screenshot=FScreenshotRequest::IsScreenshotRequested();
    State->Samples.Add(S);State->Current=S;UEFogMobilePerformanceBoundary(Graph,0);
#endif
}
void UEFogMobilePerformanceBoundary(FRDGBuilder& Graph,uint32 Boundary)
{
#if PLATFORM_ANDROID
    using namespace MobilePerf;if(!State||!State->Current||Boundary>=QueriesPerFrame)return;
    auto S=State->Current;auto V=State->Native;
    // A parameterless RDG pass must use None; RDG supplies NeverCull and runs it
    // on the graphics pipe. Compute is rejected by 5.8.4 RDG validation.
    Graph.AddPass(RDG_EVENT_NAME("UEFog.Perf.Timestamp.%u",Boundary),ERDGPassFlags::None,
        [S,V,Boundary](FRHICommandList& List)
        {
            List.EnqueueLambda([S,V,Boundary](FRHICommandListBase& Executing)
            {
                check(Executing.GetPipeline()==ERHIPipeline::Graphics);
                const VkCommandBuffer Command=V->RHI->RHIGetVkCommandBuffer(Executing);
                // The entire pool's reset completed before ready/arm. Every
                // slot is used exactly once, so pending writes remain legally
                // initialized/unavailable even during the drain phase.
                V->Timestamp(Command,VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,V->Pool,S->Slot*QueriesPerFrame+Boundary);
                S->Issued.fetch_or(1u<<Boundary,std::memory_order_release);
            });
        });
#endif
}
bool UEFogMobilePerformanceNeedsSnapshot()
{
#if PLATFORM_ANDROID
    return MobilePerf::State&&MobilePerf::State->Current&&!MobilePerf::State->Snapshot;
#else
    return false;
#endif
}
void UEFogMobilePerformanceWitness(TSharedPtr<FJsonObject> Snapshot,bool Same,uint32 I,uint32 R,uint32 C)
{
#if PLATFORM_ANDROID
    using namespace MobilePerf;if(!State||!State->Current)return;
    auto S=State->Current;S->Witness=true;S->SameResource=Same;S->Integrate=I;S->Reconstruct=R;S->Composite=C;
    if(Snapshot)State->Snapshot=Snapshot;
#endif
}
void UEFogMobilePerformanceEnd(FRDGBuilder& Graph,const FSceneView& View)
{ UEFogMobilePerformanceBoundary(Graph,5); }
void ShutdownUEFogMobilePerformance()
{
#if PLATFORM_ANDROID
    using namespace MobilePerf;if(!State)return;
    FCoreDelegates::ApplicationWillDeactivateDelegate.Remove(State->Deactivate);FCoreDelegates::ApplicationWillEnterBackgroundDelegate.Remove(State->Background);
    // Teardown only, outside every timing window. Never wait for a query per frame.
    FlushRenderingCommands();
    if(auto V=State->Native)
    {ENQUEUE_RENDER_COMMAND(UEFogPerfDestroy)([V](FRHICommandListImmediate& L){L.SubmitAndBlockUntilGPUIdle();V->Destroy(V->Device,V->Pool,nullptr);});FlushRenderingCommands();}
    State.Reset();
#endif
}
