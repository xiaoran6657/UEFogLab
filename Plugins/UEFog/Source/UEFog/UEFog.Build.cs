using UnrealBuildTool;
using System.IO;

public class UEFog : ModuleRules
{
    public UEFog(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "Json" });
        PrivateDependencyModuleNames.AddRange(new[] { "CoreUObject", "Engine", "Projects", "RenderCore", "Renderer", "RHI" });
        PrivateIncludePaths.Add(Path.Combine(EngineDirectory, "Source/Runtime/Renderer/Private"));
        PrivateIncludePaths.Add(Path.Combine(EngineDirectory, "Source/Runtime/Renderer/Internal"));
        if (Target.Platform == UnrealTargetPlatform.Android)
        {
            PrivateDependencyModuleNames.Add("VulkanRHI");
            AddEngineThirdPartyPrivateStaticDependencies(Target, "Vulkan");
        }
    }
}
