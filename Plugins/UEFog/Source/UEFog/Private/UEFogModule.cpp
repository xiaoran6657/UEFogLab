#include "Modules/ModuleManager.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/CoreDelegates.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "HAL/IConsoleManager.h"
#include "ShaderCore.h"
#include "UEFogViewExtension.h"
#include "UEFogLighting.h"
#include "UEFogCompare.h"
#include "UEFogMobileStartup.h"
#include "UEFogMobileProbe.h"
#include "UEFogMobileFog.h"
#include "UEFogMobileReconstruction.h"
#include "UEFogMobilePerformance.h"

class FUEFogModule final : public IModuleInterface
{
public:
    virtual void StartupModule() override
    {
        // ExecCmds is too late for first-frame diagnostics. Apply launch settings
        // before registering the view extension and before rendering starts.
        const TCHAR* LaunchKeys[] = { TEXT("FogLabEnabled="), TEXT("FogLabSteps="), TEXT("FogLabDownsample="), TEXT("FogLabReconstruction="), TEXT("FogLabDensityMode=") };
        const TCHAR* ConsoleKeys[] = { TEXT("r.UEFog.Enabled"), TEXT("r.UEFog.Steps"), TEXT("r.UEFog.Downsample"), TEXT("r.UEFog.Reconstruction"), TEXT("r.UEFog.DensityMode") };
        for (int32 Index = 0; Index < UE_ARRAY_COUNT(LaunchKeys); ++Index)
        {
            int32 Value = 0;
            if (FParse::Value(FCommandLine::Get(), LaunchKeys[Index], Value))
            {
                IConsoleVariable* Variable = IConsoleManager::Get().FindConsoleVariable(ConsoleKeys[Index]);
                check(Variable);
                Variable->Set(Value, ECVF_SetByCommandline);
            }
        }
        const TCHAR* FloatKeys[] = { TEXT("FogLabExtinction="), TEXT("FogLabEarlyOut="), TEXT("FogLabPreExposure=") };
        const TCHAR* FloatCVars[] = { TEXT("r.UEFog.Extinction"), TEXT("r.UEFog.EarlyOut"), TEXT("r.EyeAdaptation.PreExposureOverride") };
        for (int32 Index = 0; Index < UE_ARRAY_COUNT(FloatKeys); ++Index)
        {
            float Value = 0;
            if (FParse::Value(FCommandLine::Get(), FloatKeys[Index], Value))
            {
                if (IConsoleVariable* Variable = IConsoleManager::Get().FindConsoleVariable(FloatCVars[Index]))
                    Variable->Set(Value, ECVF_SetByCommandline);
            }
        }
        const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEFog"));
        check(Plugin.IsValid());
        AddShaderSourceDirectoryMapping(TEXT("/Plugin/UEFog"), FPaths::Combine(Plugin->GetBaseDir(), TEXT("Shaders")));
        // Global shader types still need their directory mapping while UE exits
        // after a bad request. Do not register a rendering extension on failure.
        FString LightingError;
        if (!ApplyUEFogLightingLaunch(LightingError) || !ApplyUEFogCompareLaunch(LightingError) || !ApplyUEFogMobilePerformanceLaunch(LightingError) || !ApplyUEFogMobileStartupLaunch(LightingError) || !ApplyUEFogMobileProbeLaunch(LightingError) || !ApplyUEFogMobileFogLaunch(LightingError) || !ApplyUEFogMobileReconstructionLaunch(LightingError))
        {
            UE_LOG(LogTemp, Error, TEXT("UEFog launch rejected: %s"), *LightingError);
            FPlatformMisc::RequestExitWithStatus(false, 2);
            return;
        }
        InitHandle = FCoreDelegates::GetOnPostEngineInit().AddRaw(this, &FUEFogModule::OnEngineReady);
    }
    virtual void ShutdownModule() override
    {
        FCoreDelegates::GetOnPostEngineInit().Remove(InitHandle);
        Extension.Reset();
        ShutdownUEFogMobileStartup();
        MobileStartupExtension.Reset();
        MobileProbeExtension.Reset();
        MobileFogExtension.Reset();
        MobileReconstructionExtension.Reset();
        ShutdownUEFogMobilePerformance();
        ShutdownUEFogMobileReconstruction();
    }
private:
    void OnEngineReady()
    {
        UEFogMobileProbePrepareCookContent();
        Extension = FSceneViewExtensions::NewExtension<FUEFogViewExtension>();
        MobileStartupExtension = CreateUEFogMobileStartupExtension();
        MobileProbeExtension = CreateUEFogMobileProbeExtension();
        MobileFogExtension = CreateUEFogMobileFogExtension();
        MobileReconstructionExtension = CreateUEFogMobileReconstructionExtension();
    }
    FDelegateHandle InitHandle;
    TSharedPtr<FUEFogViewExtension, ESPMode::ThreadSafe> Extension;
    TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> MobileStartupExtension;
    TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> MobileProbeExtension;
    TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> MobileFogExtension;
    TSharedPtr<ISceneViewExtension, ESPMode::ThreadSafe> MobileReconstructionExtension;
};

IMPLEMENT_MODULE(FUEFogModule, UEFog)
