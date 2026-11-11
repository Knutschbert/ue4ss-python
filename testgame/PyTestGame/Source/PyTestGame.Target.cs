using UnrealBuildTool;

public class PyTestGameTarget : TargetRules
{
	public PyTestGameTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V2;
		// Visual Studio 2019's own toolchain (UBT keys toolchains by cl.exe product version, here VS2019 Community 14.29.30151; otherwise it takes the newest VS on the machine).
		WindowsPlatform.Compiler = WindowsCompiler.VisualStudio2019;
		WindowsPlatform.CompilerVersion = "14.29.30151";
		// The engine is an installed build with prebuilt Shipping binaries, so the default shared build
		// environment is used: only this project's module is compiled and linked against them.
		ExtraModuleNames.Add("PyTestGame");
	}
}
