using System.IO.Compression;
using HostForge.TestInfra;

namespace HostForge.AvaloniaAppHost.Tests;

[WindowsOnly]
public class AvaloniaAppHostTests
{
    private static readonly string[] AllRids = ["win-x64", "win-arm64"];

    [Before(Class)]
    public static async Task PackAvaloniaPackage()
    {
        await AvaloniaPackageBuilder.EnsurePackedAsync();
    }

    private static IReadOnlyList<string> GetPackedRids()
    {
        return AllRids.Where(rid => File.Exists(NupkgPath(RepoContext.GetAvaloniaRidPackageId(rid)))).ToList();
    }

    private static string NupkgPath(string packageId) =>
        Path.Combine(RepoContext.AvaloniaPackageOutputDir, $"{packageId}.{RepoContext.AvaloniaPackageIdentityVersion}.nupkg");

    private static void UnpackNupkg(string nupkgPath, string tempDir)
    {
        if (Directory.Exists(tempDir))
            Directory.Delete(tempDir, recursive: true);
        ZipFile.ExtractToDirectory(nupkgPath, tempDir);
    }

    private static HashSet<string> ListFiles(string root)
    {
        return Directory.GetFiles(root, "*", SearchOption.AllDirectories)
            .Select(f => f[(root.Length + 1)..].Replace('\\', '/'))
            .Where(f => !f.EndsWith(".nuspec", StringComparison.Ordinal)
                        && !f.EndsWith(".psmdcp", StringComparison.Ordinal)
                        && f != ".signature.p7s"
                        && f != "README.md"
                        && f != "README.txt")
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
    }

    [Test]
    public async Task RidPackages_ContainOwnTemplatesAndProps()
    {
        var packedRids = GetPackedRids();
        if (packedRids.Count == 0)
            throw new InvalidOperationException("No RID packages found.");

        foreach (string rid in packedRids)
        {
            string packageId = RepoContext.GetAvaloniaRidPackageId(rid);
            string nupkg = NupkgPath(packageId);
            AssertEx.FileExists(nupkg);

            string tmp = Path.Combine(Path.GetTempPath(), $"hft-{Guid.NewGuid():N}");
            try
            {
                UnpackNupkg(nupkg, tmp);
                HashSet<string> files = ListFiles(tmp);

                if (!files.Contains($"template/net10.0/{rid}/apphost.exe"))
                    throw new InvalidOperationException($"{rid} package missing apphost");
                if (!files.Contains($"template/net10.0/{rid}/singlefilehost.exe"))
                    throw new InvalidOperationException($"{rid} package missing singlefilehost");

                foreach (string other in AllRids)
                {
                    if (other == rid) continue;
                    if (files.Any(f => f.Contains($"/{other}/")))
                        throw new InvalidOperationException($"{rid} package leaked {other} templates");
                }

                if (!files.Contains($"buildTransitive/{packageId}.props"))
                    throw new InvalidOperationException($"{rid} package missing .props");
            }
            finally
            {
                if (Directory.Exists(tmp))
                    Directory.Delete(tmp, recursive: true);
            }
        }
    }

    [Test]
    public async Task BuildPackage_ContainsTargetsAndModuleInitializer()
    {
        string packageId = RepoContext.AvaloniaAppHostBuildPackageId;
        string nupkg = NupkgPath(packageId);
        AssertEx.FileExists(nupkg);

        string tmp = Path.Combine(Path.GetTempPath(), $"hft-{Guid.NewGuid():N}");
        try
        {
            UnpackNupkg(nupkg, tmp);
            HashSet<string> files = ListFiles(tmp);

            if (!files.Contains("buildTransitive/ChsBuffer.Avalonia.AppHost.Build.targets"))
                throw new InvalidOperationException("Build package missing .targets");
            if (!files.Contains("buildTransitive/ModuleInitializer.cs"))
                throw new InvalidOperationException("Build package missing ModuleInitializer.cs");
            if (files.Any(f => f.StartsWith("template/", StringComparison.Ordinal)))
                throw new InvalidOperationException("Build package must not contain templates");
        }
        finally
        {
            if (Directory.Exists(tmp))
                Directory.Delete(tmp, recursive: true);
        }
    }

    [Test]
    public async Task WindowsAnglePInvoke_ResolvesFromAppHost_WinX64()
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: "net10.0",
            runtimeIdentifier: "win-x64",
            includeNativeAssetsPackages: true);

        CommandResult result = await RunDotNetCommandAsync(
            "publish",
            project.ProjectFilePath,
            project.ProjectDirectory);

        AssertEx.Success(result, "windows-angle-pinvoke:publish");
        string outputDirectory = project.GetPublishDirectory("Release", "net10.0", "win-x64");
        string executable = Path.Combine(outputDirectory, $"{project.ProjectName}.exe");
        string angleDll = Path.Combine(outputDirectory, "av_libglesv2.dll");
        AssertEx.FileMissing(angleDll);

        CommandResult runResult = await CommandRunner.RunAsync(
            executable,
            string.Empty,
            outputDirectory);
        AssertEx.Success(runResult, "windows-angle-pinvoke:run");
        AssertEx.Contains(runResult.CombinedOutput, "AngleError=0x");
    }

    [Test]
    public async Task PublishRules_CoverNet9Net10AndAvailableRids()
    {
        var packedRids = GetPackedRids();
        var cases = new List<PublishRuleCase>();

        foreach (string rid in packedRids)
        {
            cases.Add(new($"net9_publish_{rid}", "net9.0", rid, ExpectedActive: false, ExpectNativeRuntimeCopy: true, RunExecutable: rid == "win-x64"));
            cases.Add(new($"net10_publish_{rid}", "net10.0", rid, ExpectedActive: true, ExpectNativeRuntimeCopy: false, RunExecutable: rid == "win-x64"));
        }

        PublishRuleCase[] allCases = cases.ToArray();

        foreach (PublishRuleCase testCase in allCases)
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
                AssertNativeRuntimeCopy(
                    outputDirectory,
                    testCase.ExpectNativeRuntimeCopy,
                    testCase.ExpectNativeRuntimeCopy);

                if (testCase.RunExecutable)
                {
                    string exePath = Path.Combine(outputDirectory, $"{project.ProjectName}.exe");
                    await RunExeAndAssertSuccess(testCase.Name, exePath);
                }
            });
        }
    }

    [Test]
    public async Task DisableSkiaHarfBuzzRuntimeCopy_AffectsPublishOutputs_WinX64()
    {
        SwitchRuleCase[] cases =
        [
            new("net10_publish_default", DisableSkiaHarfBuzzRuntimeCopy: null, DisableAngleRuntimeCopy: null, ExpectSkiaHarfBuzzRuntimeCopy: false, ExpectAngleRuntimeCopy: false),
            new("net10_publish_skia-copy-switch-false", DisableSkiaHarfBuzzRuntimeCopy: false, DisableAngleRuntimeCopy: null, ExpectSkiaHarfBuzzRuntimeCopy: true, ExpectAngleRuntimeCopy: false),
            new("net10_publish_angle-copy-switch-false", DisableSkiaHarfBuzzRuntimeCopy: null, DisableAngleRuntimeCopy: false, ExpectSkiaHarfBuzzRuntimeCopy: false, ExpectAngleRuntimeCopy: true)
        ];

        foreach (SwitchRuleCase testCase in cases)
        {
            await ExecuteCaseAsync(testCase.Name, async () =>
            {
                await using var project = await TestProjectWorkspace.CreateAsync(
                    targetFramework: "net10.0",
                    runtimeIdentifier: "win-x64",
                    includeNativeAssetsPackages: true,
                    disableSkiaHarfBuzzRuntimeCopy: testCase.DisableSkiaHarfBuzzRuntimeCopy,
                    disableAngleRuntimeCopy: testCase.DisableAngleRuntimeCopy);

                CommandResult result = await RunDotNetCommandAsync("publish", project.ProjectFilePath, project.ProjectDirectory);

                AssertEx.Success(result, testCase.Name);
                AssertActivationState(result.CombinedOutput, expectedActive: true, "net10.0", "win-x64");

                string outputDirectory = project.GetPublishDirectory("Release", "net10.0", "win-x64");

                AssertNativeRuntimeCopy(
                    outputDirectory,
                    testCase.ExpectSkiaHarfBuzzRuntimeCopy,
                    testCase.ExpectAngleRuntimeCopy);
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
            $"{verb} \"{projectFilePath}\" -c Release -v:minimal",
            workingDirectory);
    }

    private static void AssertNativeRuntimeCopy(
        string outputDirectory,
        bool expectSkiaHarfBuzzRuntimeCopy,
        bool expectAngleRuntimeCopy)
    {
        string skiaDllPath = Path.Combine(outputDirectory, "libSkiaSharp.dll");
        string harfBuzzDllPath = Path.Combine(outputDirectory, "libHarfBuzzSharp.dll");
        string angleDllPath = Path.Combine(outputDirectory, "av_libglesv2.dll");

        if (expectSkiaHarfBuzzRuntimeCopy)
        {
            AssertEx.FileExists(skiaDllPath);
            AssertEx.FileExists(harfBuzzDllPath);
        }
        else
        {
            AssertEx.FileMissing(skiaDllPath);
            AssertEx.FileMissing(harfBuzzDllPath);
        }

        if (expectAngleRuntimeCopy)
        {
            AssertEx.FileExists(angleDllPath);
        }
        else
        {
            AssertEx.FileMissing(angleDllPath);
        }
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
        string inactiveMarker = "ChsBuffer.Avalonia.AppHost is inactive";

        if (expectedActive)
        {
            AssertEx.NotContains(combinedOutput, inactiveMarker);
            return;
        }

        AssertEx.Contains(combinedOutput, inactiveMarker);
        AssertEx.Contains(combinedOutput, $"TargetFramework='{targetFramework}'");
        AssertEx.Contains(combinedOutput, $"RuntimeIdentifier='{runtimeIdentifier}'");
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
        bool? DisableSkiaHarfBuzzRuntimeCopy,
        bool? DisableAngleRuntimeCopy,
        bool ExpectSkiaHarfBuzzRuntimeCopy,
        bool ExpectAngleRuntimeCopy);
}
