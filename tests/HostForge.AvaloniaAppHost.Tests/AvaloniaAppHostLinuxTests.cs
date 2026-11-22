using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using HostForge.TestInfra;

namespace HostForge.AvaloniaAppHost.Tests;

[LinuxOnly]
public class AvaloniaAppHostLinuxTests
{
    // The RID of the running Linux: linux-x64 on glibc, linux-musl-x64 or linux-musl-arm64 on Alpine.
    private static readonly string Rid =
        Environment.GetEnvironmentVariable("HOSTFORGE_NATIVE_RID") ?? RuntimeInformation.RuntimeIdentifier;

    private static bool IsMusl => Rid.StartsWith("linux-musl-", StringComparison.Ordinal);

    private static bool IsArm64 => Rid.EndsWith("-arm64", StringComparison.Ordinal);

    [Before(Class)]
    public static async Task PackAvaloniaPackage()
    {
        await AvaloniaPackageBuilder.EnsurePackedAsync();
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task PublishLinux_RunsAndPrintsNativeVersion(string targetFramework)
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: Rid,
            includeNativeAssetsPackages: false);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"publish \"{project.ProjectFilePath}\" -c Release -r {Rid} -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result, $"{Rid}-publish ({targetFramework})");
        AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

        string executablePath = Path.Combine(
            project.GetPublishDirectory("Release", targetFramework, Rid),
            project.ProjectName);

        AssertEx.FileExists(executablePath);

        CommandResult runResult = await CommandRunner.RunAsync(
            executablePath,
            string.Empty,
            Path.GetDirectoryName(executablePath)!);

        AssertEx.Success(runResult, $"{Rid}-run");
        AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaSingleFilePublishCases))]
    public async Task SingleFilePublish_RunsOnPrelinkedSingleFileHost_Linux(string targetFramework, bool includeNativeLibrariesForSelfExtract)
    {
        string caseName = $"{targetFramework}{(includeNativeLibrariesForSelfExtract ? ", self-extract" : string.Empty)}";
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: Rid,
            includeNativeAssetsPackages: true,
            publishSingleFile: true);

        string selfExtractArgument = includeNativeLibrariesForSelfExtract ? " -p:IncludeNativeLibrariesForSelfExtract=true" : string.Empty;
        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"publish \"{project.ProjectFilePath}\" -c Release -r {Rid} -v:minimal{selfExtractArgument}",
            project.ProjectDirectory);

        AssertEx.Success(result, $"{Rid} single-file publish ({caseName})");
        AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

        // The bundle runs on the runtime embedded in the template, with Skia and HarfBuzz
        // resolved from the executable instead of extracted or adjacent native libraries.
        string outputDirectory = project.GetPublishDirectory("Release", targetFramework, Rid);
        AssertNativeRuntimeCopy(outputDirectory, expectNativeRuntimeCopy: false);

        string extractionDirectory = Path.Combine(project.ProjectDirectory, "bundle-extraction");
        CommandResult runResult = await CommandRunner.RunAsync(
            Path.Combine(outputDirectory, project.ProjectName),
            string.Empty,
            outputDirectory,
            environmentVariables: new Dictionary<string, string> { ["DOTNET_BUNDLE_EXTRACT_BASE_DIR"] = extractionDirectory });

        AssertEx.Success(runResult, $"{Rid} single-file run ({caseName})");

        // The host extracts bundled native libraries to <base>/<app>/<bundle id>/ at startup.
        if (Directory.Exists(extractionDirectory))
        {
            foreach (string directory in Directory.EnumerateDirectories(extractionDirectory, "*", SearchOption.AllDirectories))
                AssertNativeRuntimeCopy(directory, expectNativeRuntimeCopy: false);
        }

        AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
        // The bundle pins its managed framework to the runtime embedded in the template.
        AssertEx.Contains(runResult.CombinedOutput, $"RuntimeVersion={RepoContext.GetHostLibsVersion(targetFramework)}");
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task BuildOutput_RunsOnTheTemplateAndOnDotNet_Linux(string targetFramework)
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: Rid,
            includeNativeAssetsPackages: true);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"build \"{project.ProjectFilePath}\" -c Release -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result, $"{Rid} build ({targetFramework})");
        AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

        string outputDirectory = GetBuildOutputDirectory(project, targetFramework, Rid);
        CommandResult templateRun = await CommandRunner.RunAsync(
            Path.Combine(outputDirectory, project.ProjectName), string.Empty, outputDirectory);
        AssertEx.Success(templateRun, $"{Rid} build output run ({targetFramework})");
        AssertLoadedSkiaSharpNativeVersion(templateRun.CombinedOutput);

        // Under dotnet the main program exports none of the libraries, so the resolver of the application
        // assembly leaves them to the files in the build output, as for a test project that references it.
        CommandResult dotnetRun = await CommandRunner.RunAsync("dotnet", $"\"{project.ProjectName}.dll\"", outputDirectory);
        AssertEx.Success(dotnetRun, $"{Rid} build output on dotnet ({targetFramework})");
        AssertLoadedSkiaSharpNativeVersion(dotnetRun.CombinedOutput);
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task PublishWithoutRuntimeIdentifier_KeepsTheNativeLibraries_Linux(string targetFramework)
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: Rid,
            includeNativeAssetsPackages: true,
            setRuntimeIdentifier: false);

        CommandResult result = await CommandRunner.RunAsync(
            "dotnet",
            $"publish \"{project.ProjectFilePath}\" -c Release -v:minimal",
            project.ProjectDirectory);

        AssertEx.Success(result, $"{targetFramework} publish without a RID");
        // dotnet run and IDE builds have no RID either, so the inactive package stays silent.
        AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

        string outputDirectory = project.GetPublishDirectory("Release", targetFramework, string.Empty);
        foreach (string library in new[] { "libSkiaSharp.so", "libHarfBuzzSharp.so" })
        {
            if (!Directory.EnumerateFiles(Path.Combine(outputDirectory, "runtimes"), library, SearchOption.AllDirectories).Any())
                throw new InvalidOperationException($"The portable publish output has no {library} under runtimes/.");
        }

        // The portable output runs on dotnet and loads the libraries of the running RID from runtimes/.
        CommandResult runResult = await CommandRunner.RunAsync("dotnet", $"\"{project.ProjectName}.dll\"", outputDirectory);
        AssertEx.Success(runResult, $"{targetFramework} run without a RID");
        AssertLoadedSkiaSharpNativeVersion(runResult.CombinedOutput);
    }

    [Test]
    public async Task DisableSkiaHarfBuzzRuntimeCopy_AffectsPublishOutputs_Linux()
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
                    runtimeIdentifier: Rid,
                    includeNativeAssetsPackages: true,
                    disableSkiaHarfBuzzRuntimeCopy: testCase.DisableSkiaHarfBuzzRuntimeCopy);

                CommandResult result = await CommandRunner.RunAsync(
                    "dotnet",
                    $"{testCase.Verb} \"{project.ProjectFilePath}\" -c Release -r {Rid} -v:minimal",
                    project.ProjectDirectory);

                AssertEx.Success(result, testCase.Name);
                AssertEx.NotContains(result.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

                string outputDirectory = testCase.Verb == "publish"
                    ? project.GetPublishDirectory("Release", "net10.0", Rid)
                    : GetBuildOutputDirectory(project, "net10.0", Rid);

                AssertNativeRuntimeCopy(outputDirectory, testCase.ExpectNativeRuntimeCopy);
            });
        }
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task LinkedTemplates_UseTheLibcOfTheRid(string targetFramework)
    {
        string templateDirectory = Path.Combine(
            RepoContext.RepoRoot, "artifacts", "avalonia-host", RepoContext.TargetAvaloniaVersion, targetFramework, Rid);

        foreach (string template in new[] { "apphost", "singlefilehost" })
        {
            string path = Path.Combine(templateDirectory, template);
            AssertEx.FileExists(path);
            CommandResult readelf = await CommandRunner.RunAsync("readelf", $"-h -l -d -V \"{path}\"", templateDirectory);
            AssertEx.Success(readelf, $"ELF audit of {Rid} {template} ({targetFramework})");
            AuditElf(readelf.StandardOutput, $"{Rid} {template} ({targetFramework})");
        }
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task LinkedSingleFileHost_ExportsTheDiagnosticsSymbols(string targetFramework)
    {
        string path = Path.Combine(
            RepoContext.RepoRoot, "artifacts", "avalonia-host", RepoContext.TargetAvaloniaVersion, targetFramework, Rid,
            "singlefilehost");
        AssertEx.FileExists(path);
        CommandResult readelf = await CommandRunner.RunAsync("readelf", $"--dyn-syms -W \"{path}\"", Path.GetDirectoryName(path)!);
        AssertEx.Success(readelf, $"dynamic symbols of {Rid} singlefilehost ({targetFramework})");

        // The exports of the upstream singlefilehost, defined (not UND) in the dynamic symbol table.
        foreach (string export in new[] { "DotNetRuntimeInfo", "g_dacTable", "MetaDataGetDispenser", "DotNetRuntimeContractDescriptor" })
        {
            if (!Regex.IsMatch(readelf.StandardOutput, $@"\sDEFAULT\s+\d+\s+{export}(@|\s|$)", RegexOptions.Multiline))
                throw new InvalidOperationException(
                    $"{Rid} singlefilehost ({targetFramework}) does not export {export}.{Environment.NewLine}{readelf.StandardOutput}");
        }
    }

    private static void AuditElf(string readelf, string template)
    {
        if (!Regex.IsMatch(readelf, IsArm64 ? @"Machine:\s+AArch64" : @"Machine:\s+Advanced Micro Devices X86-64"))
            throw new InvalidOperationException($"{template} is not an ELF for {Rid}.{Environment.NewLine}{readelf}");
        if (!IsMusl)
        {
            AssertEx.Contains(readelf, IsArm64 ? "/lib/ld-linux-aarch64.so.1" : "/lib64/ld-linux-x86-64.so.2");
            return;
        }

        AssertEx.Contains(readelf, IsArm64 ? "/lib/ld-musl-aarch64.so.1" : "/lib/ld-musl-x86_64.so.1");
        if (Regex.IsMatch(readelf, @"lib(c|m)\.so\.6|lib(pthread|dl|rt)\.so\.[012]|ld-linux"))
            throw new InvalidOperationException($"{template} depends on glibc.{Environment.NewLine}{readelf}");
        string provider = string.Empty;
        foreach (string line in readelf.Split('\n'))
        {
            Match file = Regex.Match(line, @"\bFile: (\S+)");
            if (file.Success)
                provider = file.Groups[1].Value;
            Match version = Regex.Match(line, @"\bName: (GLIBC_\S+)");
            // GCC's libgcc-glibc.ver also labels musl unwind exports GLIBC_2.0.
            if (version.Success && (provider != "libgcc_s.so.1" || version.Groups[1].Value != "GLIBC_2.0"))
                throw new InvalidOperationException($"{template} requires {version.Groups[1].Value} from {provider}.");
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
