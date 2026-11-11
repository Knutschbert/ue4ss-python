using UnrealBuildTool;

public class PyTestGameEditorTarget : TargetRules
{
	public PyTestGameEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V2;
		// Visual Studio 2019's own toolchain (UBT keys toolchains by cl.exe product version, here VS2019 Community 14.29.30151; otherwise it takes the newest VS on the machine).
		WindowsPlatform.Compiler = WindowsCompiler.VisualStudio2019;
		WindowsPlatform.CompilerVersion = "14.29.30151";
		ExtraModuleNames.Add("PyTestGame");
	}
}
