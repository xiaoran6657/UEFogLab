using UnrealBuildTool;

public class UEFogLabTarget : TargetRules
{
    public UEFogLabTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.V7;
        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
        ExtraModuleNames.Add("FogLabDemo");
    }
}
