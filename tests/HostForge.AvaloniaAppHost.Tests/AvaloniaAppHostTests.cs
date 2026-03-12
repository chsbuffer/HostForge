using System.Xml.Linq;
using HostForge.TestInfra;

namespace HostForge.AvaloniaAppHost.Tests;

[WindowsOnly]
public class AvaloniaAppHostTests
{
    [Before(Class)]
    public static async Task PackAvaloniaPackage()
    {
        await AvaloniaPackageBuilder.EnsurePackedAsync("windows");
    }

    [Test]
    public async Task PackageContract_MatchesCurrentTargetAvaloniaVersion()
    {
        string nuspecPath = Path.Combine(
            RepoContext.RepoRoot,
            "src",
            "package-avalonia-apphost",
            "obj",
            "Release",
            $"{RepoContext.GetAvaloniaPackageId("windows")}.{RepoContext.AvaloniaPackageIdentityVersion}.nuspec");

        AssertEx.FileExists(nuspecPath);

        string nupkgPath = Path.Combine(
            RepoContext.AvaloniaPackageOutputDir,
            $"{RepoContext.GetAvaloniaPackageId("windows")}.{RepoContext.AvaloniaPackageIdentityVersion}.nupkg");

        AssertEx.FileExists(nupkgPath);

        XDocument nuspec = XDocument.Load(nuspecPath);
        XNamespace ns = nuspec.Root?.Name.Namespace ?? XNamespace.None;

        AssertElementValue(nuspec, ns, "metadata", "version", RepoContext.AvaloniaPackageVersion);
        AssertDependencyVersion(nuspec, ns, "SkiaSharp", RepoContext.SkiaSharpVersion);
        AssertDependencyVersion(nuspec, ns, "HarfBuzzSharp", RepoContext.HarfBuzzVersion);

        string[] expectedTemplateTargets =
        [
            @"template\net10.0\win-x64\apphost.exe",
            @"template\net10.0\win-x64\singlefilehost.exe",
            @"template\net10.0\win-arm64\apphost.exe",
            @"template\net10.0\win-arm64\singlefilehost.exe"
        ];

        HashSet<string> actualTargets = nuspec
            .Descendants(ns + "file")
            .Select(x => x.Attribute("target")?.Value)
            .Where(x => !string.IsNullOrWhiteSpace(x))
            .Cast<string>()
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        foreach (string expectedTarget in expectedTemplateTargets)
        {
            if (!actualTargets.Contains(expectedTarget))
            {
                throw new InvalidOperationException($"Expected template target was not found: {expectedTarget}");
            }
        }
    }

    [Test]
    public async Task PublishRules_CoverNet9Net10AndWinX64WinArm64()
    {
        PublishRuleCase[] cases =
        [
            new("net9_publish_win-x64", "net9.0", "win-x64", ExpectedActive: false, ExpectNativeRuntimeCopy: true, RunExecutable: true),
            new("net10_publish_win-x64", "net10.0", "win-x64", ExpectedActive: true, ExpectNativeRuntimeCopy: false, RunExecutable: true),
            new("net9_publish_win-arm64", "net9.0", "win-arm64", ExpectedActive: false, ExpectNativeRuntimeCopy: true, RunExecutable: false),
            new("net10_publish_win-arm64", "net10.0", "win-arm64", ExpectedActive: true, ExpectNativeRuntimeCopy: false, RunExecutable: false)
        ];

        foreach (PublishRuleCase testCase in cases)
        {
            await ExecuteCaseAsync(testCase.Name, async () =>
            {
                await using var project = await TestProjectWorkspace.CreateAsync(
                    targetFramework: testCase.TargetFramework,
                    runtimeIdentifier: testCase.RuntimeIdentifier,
                    includeNativeAssetsPackages: true);

                CommandResult result = await RunDotNetCommandAsync("publish", project.ProjectFilePath, project.ProjectDirectory);

                AssertEx.Success(result, testCase.Name);
                AssertActivationState(result.CombinedOutput, testCase.ExpectedActive, testCase.TargetFramework, testCase.RuntimeIdentifier);

                string outputDirectory = project.GetPublishDirectory("Release", testCase.TargetFramework, testCase.RuntimeIdentifier);
                AssertNativeRuntimeCopy(outputDirectory, testCase.ExpectNativeRuntimeCopy);

                if (testCase.RunExecutable)
                {
                    string exePath = Path.Combine(outputDirectory, $"{project.ProjectName}.exe");
                    await RunExeAndAssertSuccess(testCase.Name, exePath);
                }
            });
        }
    }

    [Test]
    public async Task DisableSkiaHarfBuzzRuntimeCopy_AffectsBuildAndPublishOutputs_WinX64()
    {
        SwitchRuleCase[] cases =
        [
            new("net10_build_default", "build", DisableSkiaHarfBuzzRuntimeCopy: null, ExpectNativeRuntimeCopy: false),
            new("net10_build_copy-switch-false", "build", DisableSkiaHarfBuzzRuntimeCopy: false, ExpectNativeRuntimeCopy: true),
            new("net10_publish_copy-switch-false", "publish", DisableSkiaHarfBuzzRuntimeCopy: false, ExpectNativeRuntimeCopy: true)
        ];

        foreach (SwitchRuleCase testCase in cases)
        {
            await ExecuteCaseAsync(testCase.Name, async () =>
            {
                await using var project = await TestProjectWorkspace.CreateAsync(
                    targetFramework: "net10.0",
                    runtimeIdentifier: "win-x64",
                    includeNativeAssetsPackages: true,
                    disableSkiaHarfBuzzRuntimeCopy: testCase.DisableSkiaHarfBuzzRuntimeCopy);

                CommandResult result = await RunDotNetCommandAsync(testCase.Verb, project.ProjectFilePath, project.ProjectDirectory);

                AssertEx.Success(result, testCase.Name);
                AssertActivationState(result.CombinedOutput, expectedActive: true, "net10.0", "win-x64");

                string outputDirectory = testCase.Verb == "publish"
                    ? project.GetPublishDirectory("Release", "net10.0", "win-x64")
                    : GetBuildOutputDirectory(project, "net10.0", "win-x64");

                AssertNativeRuntimeCopy(outputDirectory, testCase.ExpectNativeRuntimeCopy);
            });
        }
    }

    private static async Task<CommandResult> RunDotNetCommandAsync(
        string verb,
        string projectFilePath,
        string workingDirectory)
    {
        return await CommandRunner.RunAsync(
            "dotnet",
            RepoContext.AppendTargetAvaloniaVersionProperty($"{verb} \"{projectFilePath}\" -c Release -v:minimal"),
            workingDirectory);
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
        string skiaDllPath = Path.Combine(outputDirectory, "libSkiaSharp.dll");
        string harfBuzzDllPath = Path.Combine(outputDirectory, "libHarfBuzzSharp.dll");

        if (expectNativeRuntimeCopy)
        {
            AssertEx.FileExists(skiaDllPath);
            AssertEx.FileExists(harfBuzzDllPath);
            return;
        }

        AssertEx.FileMissing(skiaDllPath);
        AssertEx.FileMissing(harfBuzzDllPath);
    }

    private static async Task RunExeAndAssertSuccess(string caseName, string exePath)
    {
        AssertEx.FileExists(exePath);

        CommandResult runResult = await CommandRunner.RunAsync(
            exePath,
            string.Empty,
            Path.GetDirectoryName(exePath)!);

        AssertEx.Success(runResult, $"{caseName}:run-exe");
        AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
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

    private static void AssertActivationState(
        string combinedOutput,
        bool expectedActive,
        string targetFramework,
        string runtimeIdentifier)
    {
        string inactiveMessage =
            $"ChsBuffer.Avalonia.AppHost is inactive for TargetFramework='{targetFramework}' RuntimeIdentifier='{runtimeIdentifier}'";

        if (expectedActive)
        {
            AssertEx.NotContains(combinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");
            return;
        }

        AssertEx.Contains(combinedOutput, inactiveMessage);
    }

    private static void AssertElementValue(
        XDocument document,
        XNamespace ns,
        string parentName,
        string childName,
        string expectedValue)
    {
        XElement? parent = document.Root?.Element(ns + parentName);
        XElement? child = parent?.Element(ns + childName);
        string? actualValue = child?.Value?.Trim();

        if (!string.Equals(actualValue, expectedValue, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Expected {parentName}/{childName}='{expectedValue}', but found '{actualValue ?? "<missing>"}'.");
        }
    }

    private static void AssertDependencyVersion(XDocument nuspec, XNamespace ns, string dependencyId, string expectedVersion)
    {
        XElement? dependency = nuspec
            .Descendants(ns + "dependency")
            .FirstOrDefault(x => string.Equals(x.Attribute("id")?.Value, dependencyId, StringComparison.Ordinal));

        string? actualVersion = dependency?.Attribute("version")?.Value;
        if (!string.Equals(actualVersion, expectedVersion, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Expected dependency '{dependencyId}' version '{expectedVersion}', but found '{actualVersion ?? "<missing>"}'.");
        }
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

    private sealed record PublishRuleCase(
        string Name,
        string TargetFramework,
        string RuntimeIdentifier,
        bool ExpectedActive,
        bool ExpectNativeRuntimeCopy,
        bool RunExecutable);

    private sealed record SwitchRuleCase(
        string Name,
        string Verb,
        bool? DisableSkiaHarfBuzzRuntimeCopy,
        bool ExpectNativeRuntimeCopy);
}
