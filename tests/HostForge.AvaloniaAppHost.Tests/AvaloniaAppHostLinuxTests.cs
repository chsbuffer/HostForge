using HostForge.TestInfra;

namespace HostForge.AvaloniaAppHost.Tests;

[LinuxOnly]
public class AvaloniaAppHostLinuxTests
{
    [Before(Class)]
    public static async Task PackAvaloniaPackage()
    {
        await AvaloniaPackageBuilder.EnsurePackedAsync();
    }

    [Test]
    public async Task PublishLinuxX64_RunsAndPrintsNativeVersion()
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: "net10.0",
            runtimeIdentifier: "linux-x64",
            includeNativeAssetsPackages: false);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"publish \"{project.ProjectFilePath}\" -c Release -r linux-x64 -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result, "linux-x64-publish");
        AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

        string executablePath = Path.Combine(
            project.GetPublishDirectory("Release", "net10.0", "linux-x64"),
            project.ProjectName);

        AssertEx.FileExists(executablePath);

        CommandResult runResult = await CommandRunner.RunAsync(
            executablePath,
            string.Empty,
            Path.GetDirectoryName(executablePath)!);

        AssertEx.Success(runResult, "linux-x64-run");
        AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
    }

    [Test]
    public async Task DisableSkiaHarfBuzzRuntimeCopy_AffectsPublishOutputs_LinuxX64()
    {
        SwitchRuleCase[] cases =
        [
            new("net10_publish_default_linux", "publish", DisableSkiaHarfBuzzRuntimeCopy: null, ExpectNativeRuntimeCopy: false),
            new("net10_publish_copy-switch-false_linux", "publish", DisableSkiaHarfBuzzRuntimeCopy: false, ExpectNativeRuntimeCopy: true)
        ];

        foreach (SwitchRuleCase testCase in cases)
        {
            await ExecuteCaseAsync(testCase.Name, async () =>
            {
                await using var project = await TestProjectWorkspace.CreateAsync(
                    targetFramework: "net10.0",
                    runtimeIdentifier: "linux-x64",
                    includeNativeAssetsPackages: true,
                    disableSkiaHarfBuzzRuntimeCopy: testCase.DisableSkiaHarfBuzzRuntimeCopy);

                CommandResult result = await CommandRunner.RunAsync(
                    "dotnet",
                    $"{testCase.Verb} \"{project.ProjectFilePath}\" -c Release -r linux-x64 -v:minimal",
                    project.ProjectDirectory);

                AssertEx.Success(result, testCase.Name);
                AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

                string outputDirectory = testCase.Verb == "publish"
                    ? project.GetPublishDirectory("Release", "net10.0", "linux-x64")
                    : GetBuildOutputDirectory(project, "net10.0", "linux-x64");

                AssertNativeRuntimeCopy(outputDirectory, testCase.ExpectNativeRuntimeCopy);
            });
        }
    }

    private static void AssertLoadedSkiaSharpNativeVersion(string output)
    {
        const string prefix = "SkiaSharpVersion.Native=";

        AssertEx.Contains(output, prefix);

        int start = output.IndexOf(prefix, StringComparison.Ordinal) + prefix.Length;
        int end = output.IndexOfAny(['\r', '\n'], start);
        if (end < 0)
        {
            end = output.Length;
        }

        string value = output[start..end].Trim();
        if (string.IsNullOrWhiteSpace(value) || string.Equals(value, "0.0", StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Expected SkiaSharp native version to be loaded, but got '{value}'.{Environment.NewLine}{output}");
        }
    }

    private static string GetBuildOutputDirectory(
        TestProjectWorkspace project,
        string targetFramework,
        string runtimeIdentifier)
    {
        return Path.Combine(
            project.ProjectDirectory,
            "bin",
            "Release",
            targetFramework,
            runtimeIdentifier);
    }

    private static void AssertNativeRuntimeCopy(string outputDirectory, bool expectNativeRuntimeCopy)
    {
        string skiaPath = Path.Combine(outputDirectory, "libSkiaSharp.so");
        string harfBuzzPath = Path.Combine(outputDirectory, "libHarfBuzzSharp.so");

        if (expectNativeRuntimeCopy)
        {
            AssertEx.FileExists(skiaPath);
            AssertEx.FileExists(harfBuzzPath);
            return;
        }

        AssertEx.FileMissing(skiaPath);
        AssertEx.FileMissing(harfBuzzPath);
    }

    private static async Task ExecuteCaseAsync(string caseName, Func<Task> action)
    {
        try
        {
            await action();
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException($"Case '{caseName}' failed.{Environment.NewLine}{ex.Message}", ex);
        }
    }

    private sealed record SwitchRuleCase(
        string Name,
        string Verb,
        bool? DisableSkiaHarfBuzzRuntimeCopy,
        bool ExpectNativeRuntimeCopy);
}
