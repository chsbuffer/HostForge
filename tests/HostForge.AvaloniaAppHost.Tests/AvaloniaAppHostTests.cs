using System.IO.Compression;
using System.Text.Json;
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

                foreach (string tfm in RepoContext.AvaloniaTemplateTfms)
                {
                    if (!files.Contains($"template/{tfm}/{rid}/apphost.exe"))
                        throw new InvalidOperationException($"{rid} package missing {tfm} apphost");
                    if (!files.Contains($"template/{tfm}/{rid}/singlefilehost.exe"))
                        throw new InvalidOperationException($"{rid} package missing {tfm} singlefilehost");
                }

                foreach (string other in AllRids)
                {
                    if (other == rid) continue;
                    if (files.Any(f => f.Contains($"/{other}/")))
                        throw new InvalidOperationException($"{rid} package leaked {other} templates");
                }

                string props = $"buildTransitive/{packageId}.props";
                if (!files.Contains(props))
                    throw new InvalidOperationException($"{rid} package missing .props");
                await AssertRegistrationsResolveToPackedTemplates(Path.Combine(tmp, props), rid);
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
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task WindowsAnglePInvoke_ResolvesFromAppHost_WinX64(string targetFramework)
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: "win-x64",
            includeNativeAssetsPackages: true);

        CommandResult result = await RunDotNetCommandAsync(
            "publish",
            project.ProjectFilePath,
            project.ProjectDirectory);

        AssertEx.Success(result, "windows-angle-pinvoke:publish");
        AssertActivationState(result.CombinedOutput, expectedActive: true, targetFramework, "win-x64");
        string outputDirectory = project.GetPublishDirectory("Release", targetFramework, "win-x64");
        string executable = Path.Combine(outputDirectory, $"{project.ProjectName}.exe");
        string angleDll = Path.Combine(outputDirectory, "av_libglesv2.dll");
        AssertEx.FileMissing(angleDll);

        CommandResult runResult = await CommandRunner.RunAsync(
            executable,
            string.Empty,
            outputDirectory);
        AssertEx.Success(runResult, "windows-angle-pinvoke:run");
        AssertEx.Contains(runResult.CombinedOutput, "AngleError=0x");
        AssertSkottieAndLoadedLibraries(runResult.CombinedOutput, expectPrelinked: true);
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaSingleFilePublishCases))]
    public async Task SingleFilePublish_RunsOnPrelinkedSingleFileHost_WinX64(string targetFramework, bool includeNativeLibrariesForSelfExtract)
    {
        string caseName = $"{targetFramework}_single-file{(includeNativeLibrariesForSelfExtract ? "_self-extract" : string.Empty)}";
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: "win-x64",
            includeNativeAssetsPackages: true,
            publishSingleFile: true);

        CommandResult result = await RunDotNetCommandAsync(
            "publish",
            project.ProjectFilePath,
            project.ProjectDirectory,
            includeNativeLibrariesForSelfExtract ? "-p:IncludeNativeLibrariesForSelfExtract=true" : null);
        AssertEx.Success(result, $"{caseName}:publish");
        AssertActivationState(result.CombinedOutput, expectedActive: true, targetFramework, "win-x64");

        // The bundle runs on the runtime embedded in the template, with ANGLE, Skia and HarfBuzz
        // resolved from the executable instead of extracted or adjacent native libraries.
        string outputDirectory = project.GetPublishDirectory("Release", targetFramework, "win-x64");
        AssertNativeRuntimeCopy(outputDirectory, expectSkiaHarfBuzzRuntimeCopy: false, expectAngleRuntimeCopy: false);
        string extractionDirectory = Path.Combine(project.ProjectDirectory, "bundle-extraction");
        await RunExeAndAssertSuccess(
            caseName,
            Path.Combine(outputDirectory, $"{project.ProjectName}.exe"),
            RepoContext.GetHostLibsVersion(targetFramework),
            new Dictionary<string, string> { ["DOTNET_BUNDLE_EXTRACT_BASE_DIR"] = extractionDirectory });

        // The host extracts bundled native libraries to <base>/<app>/<bundle id>/ at startup.
        if (Directory.Exists(extractionDirectory))
        {
            foreach (string directory in Directory.EnumerateDirectories(extractionDirectory, "*", SearchOption.AllDirectories))
                AssertNativeRuntimeCopy(directory, expectSkiaHarfBuzzRuntimeCopy: false, expectAngleRuntimeCopy: false);
        }
    }

    [Test]
    public async Task PublishRules_CoverTemplateTfmsAndAvailableRids()
    {
        var packedRids = GetPackedRids();
        var cases = new List<PublishRuleCase>();

        foreach (string rid in packedRids)
        {
            cases.Add(new($"net9.0_publish_{rid}", "net9.0", rid, ExpectedActive: false, ExpectNativeRuntimeCopy: true, RunExecutable: rid == "win-x64"));
            foreach (string tfm in RepoContext.AvaloniaTemplateTfms)
                cases.Add(new($"{tfm}_publish_{rid}", tfm, rid, ExpectedActive: true, ExpectNativeRuntimeCopy: false, RunExecutable: rid == "win-x64"));
        }

        if (packedRids.Contains("win-x64"))
        {
            // Templates depend only on the .NET version, so an OS-specific TFM selects the same template.
            string windowsTfm = $"{RepoContext.AvaloniaTemplateTfms[^1]}-windows";
            cases.Add(new($"{windowsTfm}_publish_win-x64", windowsTfm, "win-x64", ExpectedActive: true, ExpectNativeRuntimeCopy: false, RunExecutable: true));
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
                    await RunExeAndAssertSuccess(testCase.Name, exePath, expectPrelinked: testCase.ExpectedActive);
                }
            });
        }
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task BuildOutput_RunsOnTheTemplateAndOnDotNet_WinX64(string targetFramework)
    {
        string caseName = $"{targetFramework}_build_win-x64";
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: "win-x64",
            includeNativeAssetsPackages: true);

        CommandResult result = await RunDotNetCommandAsync("build", project.ProjectFilePath, project.ProjectDirectory);
        AssertEx.Success(result, caseName);
        AssertActivationState(result.CombinedOutput, expectedActive: true, targetFramework, "win-x64");

        string outputDirectory = Path.Combine(project.ProjectDirectory, "bin", "Release", targetFramework, "win-x64");
        await RunExeAndAssertSuccess(caseName, Path.Combine(outputDirectory, $"{project.ProjectName}.exe"));

        // Under dotnet the main program exports none of the libraries, so the resolver of the application
        // assembly leaves them to the files in the build output, as for a test project that references it.
        CommandResult runResult = await CommandRunner.RunAsync("dotnet", $"\"{project.ProjectName}.dll\"", outputDirectory);
        AssertEx.Success(runResult, $"{caseName}:dotnet");
        AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
        AssertSkottieAndLoadedLibraries(runResult.CombinedOutput, expectPrelinked: false);
        AssertEx.Contains(runResult.CombinedOutput, "AngleError=0x");
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task PublishWithoutRuntimeIdentifier_KeepsTheNativeLibrariesOfEveryRid(string targetFramework)
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: "win-x64",
            includeNativeAssetsPackages: true,
            setRuntimeIdentifier: false);

        CommandResult result = await RunDotNetCommandAsync("publish", project.ProjectFilePath, project.ProjectDirectory);
        AssertEx.Success(result, $"{targetFramework}_publish_no-rid");
        // dotnet run and IDE builds have no RID either, so the inactive package stays silent.
        AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

        string outputDirectory = project.GetPublishDirectory("Release", targetFramework, string.Empty);
        foreach (string rid in AllRids)
        {
            string nativeDirectory = Path.Combine(outputDirectory, "runtimes", rid, "native");
            AssertNativeRuntimeCopy(nativeDirectory, expectSkiaHarfBuzzRuntimeCopy: true, expectAngleRuntimeCopy: true);
        }

        // The portable output runs on dotnet and loads the libraries of the running RID from runtimes/.
        CommandResult runResult = await CommandRunner.RunAsync("dotnet", $"\"{project.ProjectName}.dll\"", outputDirectory);
        AssertEx.Success(runResult, $"{targetFramework}_publish_no-rid:run");
        AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
        AssertSkottieAndLoadedLibraries(runResult.CombinedOutput, expectPrelinked: false);
        AssertEx.Contains(runResult.CombinedOutput, "AngleError=0x");
    }

    [Test]
    [Arguments("-p:UseAppHost=false", "because UseAppHost is false", false)]
    [Arguments("-p:PublishAot=true", "because PublishAot is true", true)]
    public async Task PublishWithoutAppHost_StaysInactiveAndKeepsNativeLibraries_WinX64(string property, string reason, bool runExecutable)
    {
        string targetFramework = RepoContext.AvaloniaTemplateTfms[0];
        string caseName = $"{targetFramework}_publish_{property}";
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: "win-x64",
            includeNativeAssetsPackages: true);

        CommandResult result = await RunDotNetCommandAsync("publish", project.ProjectFilePath, project.ProjectDirectory, property);
        AssertEx.Success(result, caseName);
        AssertActivationState(result.CombinedOutput, expectedActive: false, targetFramework, "win-x64");
        AssertEx.Contains(result.CombinedOutput, reason);

        // Neither NativeAOT nor dotnet loads the libraries from a template, so the dynamic libraries stay.
        string outputDirectory = project.GetPublishDirectory("Release", targetFramework, "win-x64");
        AssertNativeRuntimeCopy(outputDirectory, expectSkiaHarfBuzzRuntimeCopy: true, expectAngleRuntimeCopy: true);

        CommandResult runResult = runExecutable
            ? await CommandRunner.RunAsync(Path.Combine(outputDirectory, $"{project.ProjectName}.exe"), string.Empty, outputDirectory)
            : await CommandRunner.RunAsync("dotnet", $"\"{project.ProjectName}.dll\"", outputDirectory);
        AssertEx.Success(runResult, $"{caseName}:run");
        AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
        AssertSkottieAndLoadedLibraries(runResult.CombinedOutput, expectPrelinked: false);
        AssertEx.Contains(runResult.CombinedOutput, "AngleError=0x");
    }

    [Test]
    public async Task FSharpProject_StaysInactiveAndKeepsNativeLibraries_WinX64()
    {
        string targetFramework = RepoContext.AvaloniaTemplateTfms[0];
        string projectName = $"FSharpTest_{Guid.NewGuid():N}"[..19];
        string projectDirectory = Path.Combine(RepoContext.ArtifactsTestRoot, projectName);
        string projectFilePath = Path.Combine(projectDirectory, $"{projectName}.fsproj");
        Directory.CreateDirectory(projectDirectory);
        try
        {
            await File.WriteAllTextAsync(
                Path.Combine(projectDirectory, "Program.fs"),
                "printfn \"SkiaSharpVersion.Native=%O\" SkiaSharp.SkiaSharpVersion.Native\n");
            await File.WriteAllTextAsync(projectFilePath, $$"""
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <OutputType>Exe</OutputType>
                    <TargetFramework>{{targetFramework}}</TargetFramework>
                    <RuntimeIdentifier>win-x64</RuntimeIdentifier>
                    <RestoreAdditionalProjectSources>$(RestoreAdditionalProjectSources);$(AvaloniaAppHostPackageOutputDir)</RestoreAdditionalProjectSources>
                  </PropertyGroup>
                  <ItemGroup>
                    <Compile Include="Program.fs" />
                    <PackageReference Include="{{RepoContext.GetAvaloniaRidPackageId("win-x64")}}" Version="$(AvaloniaAppHostPackageVersion)" />
                    <PackageReference Include="SkiaSharp" Version="$(SkiaSharpVersion)" />
                    <PackageReference Include="SkiaSharp.NativeAssets.Win32" Version="$(SkiaSharpVersion)" />
                  </ItemGroup>
                </Project>
                """);

            CommandResult result = await RunDotNetCommandAsync("publish", projectFilePath, projectDirectory);
            AssertEx.Success(result, "fsharp:publish");
            AssertActivationState(result.CombinedOutput, expectedActive: false, targetFramework, "win-x64");
            AssertEx.Contains(result.CombinedOutput, "because the project language is F#");

            string outputDirectory = Path.Combine(projectDirectory, "bin", "Release", targetFramework, "win-x64", "publish");
            AssertEx.FileExists(Path.Combine(outputDirectory, "libSkiaSharp.dll"));
            CommandResult runResult = await CommandRunner.RunAsync(
                Path.Combine(outputDirectory, $"{projectName}.exe"), string.Empty, outputDirectory);
            AssertEx.Success(runResult, "fsharp:run");
            AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
        }
        finally
        {
            Directory.Delete(projectDirectory, recursive: true);
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

    private static async Task AssertRegistrationsResolveToPackedTemplates(string propsPath, string rid)
    {
        // Evaluate the generated registration as MSBuild does for consumers.
        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"msbuild \"{propsPath}\" -nologo -getItem:AvaloniaAppHostRids",
            Path.GetDirectoryName(propsPath)!);
        AssertEx.Success(result, $"{rid} registration evaluation");

        using JsonDocument document = JsonDocument.Parse(result.StandardOutput);
        JsonElement[] registrations = document.RootElement.GetProperty("Items").GetProperty("AvaloniaAppHostRids").EnumerateArray().ToArray();
        string[] tfms = registrations.Select(registration => registration.GetProperty("AppHostTemplateTfm").GetString()!).ToArray();
        if (!tfms.SequenceEqual(RepoContext.AvaloniaTemplateTfms))
        {
            throw new InvalidOperationException(
                $"{rid} registers {string.Join(", ", tfms)}; expected {string.Join(", ", RepoContext.AvaloniaTemplateTfms)}.");
        }

        string packageRoot = Path.GetDirectoryName(Path.GetDirectoryName(propsPath)!)!;
        foreach (JsonElement registration in registrations)
        {
            if (registration.GetProperty("Identity").GetString() != rid)
                throw new InvalidOperationException($"{rid} package registers another RID: {registration}");

            string tfm = registration.GetProperty("AppHostTemplateTfm").GetString()!;
            foreach ((string metadata, string host) in new[] { ("AppHostTemplatePath", "apphost.exe"), ("SingleFileHostTemplatePath", "singlefilehost.exe") })
            {
                string actual = Path.GetFullPath(registration.GetProperty(metadata).GetString()!);
                string expected = Path.GetFullPath(Path.Combine(packageRoot, "template", tfm, rid, host));
                if (!string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException($"{rid} {tfm} {metadata} is '{actual}', expected '{expected}'.");
                AssertEx.FileExists(actual);
            }
        }
    }

    private static async Task<CommandResult> RunDotNetCommandAsync(
        string verb,
        string projectFilePath,
        string workingDirectory,
        string? additionalArguments = null)
    {
        return await CommandRunner.RunAsync(
            "dotnet",
            $"{verb} \"{projectFilePath}\" -c Release -v:minimal {additionalArguments}",
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
            // SkiaSharp and HarfBuzzSharp 14 ship symbol files next to their libraries.
            AssertEx.FileMissing(skiaDllPath);
            AssertEx.FileMissing(harfBuzzDllPath);
            AssertEx.FileMissing(Path.ChangeExtension(skiaDllPath, ".pdb"));
            AssertEx.FileMissing(Path.ChangeExtension(harfBuzzDllPath, ".pdb"));
        }

        if (expectAngleRuntimeCopy)
        {
            AssertEx.FileExists(angleDllPath);
        }
        else
        {
            AssertEx.FileMissing(angleDllPath);
            AssertEx.FileMissing(Path.ChangeExtension(angleDllPath, ".pdb"));
        }
    }

    private static async Task RunExeAndAssertSuccess(
        string caseName,
        string exePath,
        string? expectedRuntimeVersion = null,
        IReadOnlyDictionary<string, string>? environmentVariables = null,
        bool expectPrelinked = true)
    {
        AssertEx.FileExists(exePath);

        CommandResult runResult = await CommandRunner.RunAsync(
            exePath,
            string.Empty,
            Path.GetDirectoryName(exePath)!,
            environmentVariables: environmentVariables);

        AssertEx.Success(runResult, $"{caseName}:run-exe");
        AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
        AssertSkottieAndLoadedLibraries(runResult.CombinedOutput, expectPrelinked);
        AssertEx.Contains(runResult.CombinedOutput, "AngleError=0x");
        if (expectedRuntimeVersion is not null)
        {
            // The bundle pins its managed framework to the runtime embedded in the template.
            AssertEx.Contains(runResult.CombinedOutput, $"RuntimeVersion={expectedRuntimeVersion}");
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
        // Native reports the Skia milestone (119/156), the NuGet version's minor component.
        if (!Version.TryParse(value, out Version? nativeVersion)
            || !Version.TryParse(RepoContext.SkiaSharpVersion, out Version? expectedVersion)
            || nativeVersion.Major != expectedVersion.Minor)
        {
            throw new InvalidOperationException(
                $"Expected native SkiaSharp {RepoContext.SkiaSharpVersion}, but got '{value}'.{Environment.NewLine}{output}");
        }
    }

    private static void AssertSkottieAndLoadedLibraries(string output, bool expectPrelinked)
    {
        // SkiaSharp.Skottie, SceneGraph and Resources import libSkiaSharp themselves.
        AssertEx.Contains(output, "Skottie=True");
        AssertEx.Contains(output, "HarfBuzzLength=9");
        if (!expectPrelinked)
            return;

        const string prefix = "GraphicsLibraryFiles=";
        AssertEx.Contains(output, prefix);
        int start = output.IndexOf(prefix, StringComparison.Ordinal) + prefix.Length;
        int end = output.IndexOfAny(['\r', '\n'], start);
        string loaded = output[start..(end < 0 ? output.Length : end)].Trim();
        if (loaded.Length != 0)
        {
            throw new InvalidOperationException(
                $"The template loaded {loaded} instead of resolving it from the executable.{Environment.NewLine}{output}");
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
