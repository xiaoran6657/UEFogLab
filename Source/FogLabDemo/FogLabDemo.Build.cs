using UnrealBuildTool;

public class FogLabDemo : ModuleRules
{
    public FogLabDemo(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "InputCore", "UEFog", "Json" });
    }
}
