using UnrealBuildTool;

public class UEFogLabEditorTarget : TargetRules
{
    public UEFogLabEditorTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.V7;
        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
        ExtraModuleNames.Add("FogLabDemo");
    }
}
