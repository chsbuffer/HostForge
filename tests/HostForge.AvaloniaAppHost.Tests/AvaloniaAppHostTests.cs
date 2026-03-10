using HostForge.TestInfra;

namespace HostForge.AvaloniaAppHost.Tests;

public class AvaloniaAppHostTests
{
    // TODO: Pass TargetAvaloniaVersion environment variable as property
    // TODO: Validate SkiaSharp.Version.Native outputs are not 0.0.
    [Before(Class)]
    public static async Task PackAvaloniaPackage()
    {
        await AvaloniaPackageBuilder.EnsurePackedAsync();
    }

    [Test]
    public async Task PackageTemplates_IncludeWinX64_Arm64()
    {
        string project = Path.Combine(
            RepoContext.RepoRoot,
            "src",
            "package-avalonia-apphost",
            "AvaloniaAppHost.csproj");

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"pack \"{project}\" -c Release -v:minimal",
            RepoContext.RepoRoot);

        AssertEx.Success(result);

        string nuspecPath = Path.Combine(
            RepoContext.RepoRoot,
            "src",
            "package-avalonia-apphost",
            "obj",
            "Release",
            $"ChsBuffer.Avalonia.AppHost.{RepoContext.AvaloniaPackageVersion}.nuspec");

        AssertEx.FileExists(nuspecPath);
        string nuspec = await File.ReadAllTextAsync(nuspecPath);

        AssertEx.Contains(nuspec, @"template\net10.0\win-x64\apphost.exe");
        AssertEx.Contains(nuspec, @"template\net10.0\win-x64\singlefilehost.exe");
        AssertEx.Contains(nuspec, @"template\net10.0\win-arm64\apphost.exe");
        AssertEx.Contains(nuspec, @"template\net10.0\win-arm64\singlefilehost.exe");
    }

    [Test]
    public async Task Net9_WinX64_Build_WarnsInactive()
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: "net9.0",
            runtimeIdentifier: "win-x64",
            includeSkiaPackages: false,
            includeNativeAssetsPackages: false);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"build \"{project.ProjectFilePath}\" -c Release -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result);
        AssertEx.Contains(
            result.CombinedOutput,
            "ChsBuffer.Avalonia.AppHost is inactive for TargetFramework='net9.0' RuntimeIdentifier='win-x64'");
    }

    [Test]
    public async Task Net9_WinX64_Publish_Inactive_KeepsSkiaHarfBuzzDlls_AndExeRuns()
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: "net9.0",
            runtimeIdentifier: "win-x64",
            includeSkiaPackages: true,
            includeNativeAssetsPackages: true);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"publish \"{project.ProjectFilePath}\" -c Release -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result);
        AssertEx.Contains(
            result.CombinedOutput,
            "ChsBuffer.Avalonia.AppHost is inactive for TargetFramework='net9.0' RuntimeIdentifier='win-x64'");

        string publishDir = project.GetPublishDirectory("Release", "net9.0", "win-x64");
        AssertEx.FileExists(Path.Combine(publishDir, "libSkiaSharp.dll"));
        AssertEx.FileExists(Path.Combine(publishDir, "libHarfBuzzSharp.dll"));
        await RunExeAndAssertSuccess(Path.Combine(publishDir, $"{project.ProjectName}.exe"));
    }

    [Test]
    public async Task Net10_WinArm64_Build_ActivationDependsOnPackagedTemplate()
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: "net10.0",
            runtimeIdentifier: "win-arm64",
            includeSkiaPackages: true,
            includeNativeAssetsPackages: false);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"build \"{project.ProjectFilePath}\" -c Release -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result);

        AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

        string exePath = Path.Combine(
            project.ProjectDirectory,
            "bin",
            "Release",
            "net10.0",
            "win-arm64",
            $"{project.ProjectName}.exe");
        AssertEx.FileExists(exePath);
    }

    [Test]
    public async Task Net10_WinArm64_Publish_SkiaHarfBuzzDllsDependOnActivation()
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: "net10.0",
            runtimeIdentifier: "win-arm64",
            includeSkiaPackages: true,
            includeNativeAssetsPackages: true);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"publish \"{project.ProjectFilePath}\" -c Release -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result);

        string publishDir = project.GetPublishDirectory("Release", "net10.0", "win-arm64");
        string skia = Path.Combine(publishDir, "libSkiaSharp.dll");
        string harfbuzz = Path.Combine(publishDir, "libHarfBuzzSharp.dll");

        AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");
        AssertEx.FileMissing(skia);
        AssertEx.FileMissing(harfbuzz);
    }

    [Test]
    public async Task Net10_WinX64_Build_ActivatesWithoutInactiveWarning()
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: "net10.0",
            runtimeIdentifier: "win-x64",
            includeSkiaPackages: true,
            includeNativeAssetsPackages: false);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"build \"{project.ProjectFilePath}\" -c Release -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result);
        AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

        string exePath = Path.Combine(
            project.ProjectDirectory,
            "bin",
            "Release",
            "net10.0",
            "win-x64",
            $"{project.ProjectName}.exe");

        AssertEx.FileExists(exePath);
        await RunExeAndAssertSuccess(exePath);
    }

    [Test]
    public async Task Net10_WinX64_Publish_DefaultSuppressesSkiaAndHarfBuzzDlls()
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: "net10.0",
            runtimeIdentifier: "win-x64",
            includeSkiaPackages: true,
            includeNativeAssetsPackages: true);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"publish \"{project.ProjectFilePath}\" -c Release -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result);

        string publishDir = project.GetPublishDirectory("Release", "net10.0", "win-x64");
        AssertEx.FileMissing(Path.Combine(publishDir, "libSkiaSharp.dll"));
        AssertEx.FileMissing(Path.Combine(publishDir, "libHarfBuzzSharp.dll"));
        await RunExeAndAssertSuccess(Path.Combine(publishDir, $"{project.ProjectName}.exe"));
    }

    [Test]
    public async Task Net10_WinX64_Publish_CopySwitchFalseRestoresSkiaAndHarfBuzzDlls()
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: "net10.0",
            runtimeIdentifier: "win-x64",
            includeSkiaPackages: true,
            includeNativeAssetsPackages: true,
            disableSkiaHarfBuzzRuntimeCopy: false);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"publish \"{project.ProjectFilePath}\" -c Release -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result);

        string publishDir = project.GetPublishDirectory("Release", "net10.0", "win-x64");
        AssertEx.FileExists(Path.Combine(publishDir, "libSkiaSharp.dll"));
        AssertEx.FileExists(Path.Combine(publishDir, "libHarfBuzzSharp.dll"));
        await RunExeAndAssertSuccess(Path.Combine(publishDir, $"{project.ProjectName}.exe"));
    }

    private static async Task RunExeAndAssertSuccess(string exePath)
    {
        AssertEx.FileExists(exePath);

        CommandResult runResult = await CommandRunner.RunAsync(
            exePath,
            string.Empty,
            Path.GetDirectoryName(exePath)!);

        AssertEx.Success(runResult, "run-exe");
        AssertEx.Contains(runResult.CombinedOutput, "SkiaSharpVersion.Native=");
    }

}
