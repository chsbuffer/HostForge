using System.Runtime.InteropServices;
using System.Xml.Linq;
using HostForge.TestInfra;

namespace HostForge.AvaloniaAppHost.Tests;

[MacOSOnly]
public class AvaloniaAppHostMacOSTests
{
    private static readonly string Rid =
        Environment.GetEnvironmentVariable("HOSTFORGE_NATIVE_RID") ?? RuntimeInformation.RuntimeIdentifier;

    [Before(Class)]
    public static async Task PackAvaloniaPackage()
    {
        await AvaloniaPackageBuilder.EnsurePackedAsync();
    }

    [Test]
    public void RidPackage_NoticesCoverTheLinkedInputs()
    {
        string nupkg = Path.Combine(RepoContext.AvaloniaPackageOutputDir,
            $"{RepoContext.GetAvaloniaRidPackageId(Rid)}.{RepoContext.AvaloniaMacOSPackageIdentityVersion}.nupkg");
        AssertEx.FileExists(nupkg);
        PackageNotices.AssertCoverTemplateInputs(nupkg, Rid);
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task SingleFilePublish_RunsWithoutExtractingDylibs(string targetFramework)
    {
        foreach (bool selfContained in new[] { false, true })
        {
            await using var project = await TestProjectWorkspace.CreateAsync(
                targetFramework: targetFramework,
                runtimeIdentifier: Rid,
                includeNativeAssetsPackages: false,
                publishSingleFile: true);

            string mode = selfContained ? "--self-contained true" : "--self-contained false";
            CommandResult publish = await CommandRunner.RunAsync("dotnet",
                $"publish \"{project.ProjectFilePath}\" -c Release -r {Rid} {mode} -p:IncludeNativeLibrariesForSelfExtract=true",
                project.ProjectDirectory);
            AssertEx.Success(publish, $"{Rid} single-file publish ({targetFramework}, {mode})");
            AssertEx.NotContains(publish.CombinedOutput, "ChsBuffer.Avalonia.AppHost is inactive");

            string output = project.GetPublishDirectory("Release", targetFramework, Rid);
            AssertEx.FileMissing(Path.Combine(output, "libSkiaSharp.dylib"));
            AssertEx.FileMissing(Path.Combine(output, "libHarfBuzzSharp.dylib"));
            string executable = Path.Combine(output, project.ProjectName);
            string extraction = Path.Combine(project.ProjectDirectory, "bundle-extraction");
            CommandResult run = await CommandRunner.RunAsync(executable, string.Empty, output,
                environmentVariables: new Dictionary<string, string> { ["DOTNET_BUNDLE_EXTRACT_BASE_DIR"] = extraction });
            AssertEx.Success(run, $"{Rid} single-file run ({targetFramework}, {mode})");
            AssertEx.Contains(run.CombinedOutput, $"SkiaSharpVersion.Native={Version.Parse(RepoContext.SkiaSharpVersion).Minor}.");
            AssertEx.Contains(run.CombinedOutput, "Skottie=True");
            AssertEx.Contains(run.CombinedOutput, "HarfBuzzLength=9");
            if (selfContained)
                AssertEx.Contains(run.CombinedOutput, $"RuntimeVersion={RepoContext.GetHostLibsVersion(targetFramework)}");
            if (Directory.Exists(extraction) && Directory.EnumerateFileSystemEntries(extraction).Any())
                throw new InvalidOperationException($"The macOS bundle extracted files under {extraction}.");
        }
    }

    [Test]
    [MethodDataSource(typeof(RepoContext), nameof(RepoContext.AvaloniaTemplateTfmCases))]
    public async Task DesktopSingleFilePublish_OpensWindowWithoutExtractingDylibs(string targetFramework)
    {
        await using var project = await TestProjectWorkspace.CreateAsync(
            targetFramework: targetFramework,
            runtimeIdentifier: Rid,
            includeNativeAssetsPackages: false,
            publishSingleFile: true);

        var definition = XDocument.Load(project.ProjectFilePath);
        XNamespace ns = definition.Root!.Name.Namespace;
        definition.Root.Add(new XElement(ns + "ItemGroup",
            new XElement(ns + "PackageReference", new XAttribute("Include", "Avalonia.Desktop"),
                new XAttribute("Version", RepoContext.AvaloniaNativeVersion))));
        definition.Save(project.ProjectFilePath);
        string smokeSource = Path.Combine(RepoContext.RepoRoot,
            "tests", "HostForge.AvaloniaNative.Tests", "Smoke", "Program.cs");
        File.Copy(smokeSource, Path.Combine(project.ProjectDirectory, "Program.cs"), overwrite: true);

        foreach (bool selfContained in new[] { false, true })
        {
            string mode = selfContained ? "--self-contained true" : "--self-contained false";
            string output = Path.Combine(project.ProjectDirectory, selfContained ? "self-contained" : "framework-dependent");
            CommandResult publish = await CommandRunner.RunAsync("dotnet",
                $"publish \"{project.ProjectFilePath}\" -c Release -r {Rid} {mode} -o \"{output}\" -p:IncludeNativeLibrariesForSelfExtract=true",
                project.ProjectDirectory);
            AssertEx.Success(publish, $"{Rid} desktop publish ({targetFramework}, {mode})");
            AssertEx.FileMissing(Path.Combine(output, "libAvaloniaNative.dylib"));

            string extraction = Path.Combine(output, "bundle-extraction");
            CommandResult run = await CommandRunner.RunAsync(Path.Combine(output, project.ProjectName), string.Empty, output,
                environmentVariables: new Dictionary<string, string> { ["DOTNET_BUNDLE_EXTRACT_BASE_DIR"] = extraction });
            AssertEx.Success(run, $"{Rid} desktop run ({targetFramework}, {mode})");
            AssertEx.Contains(run.CombinedOutput, "AVN_WINDOW_OPENED");
            AssertEx.Contains(run.CombinedOutput, "AVN_FRAME_RENDERED");
            if (Directory.Exists(extraction) && Directory.EnumerateFileSystemEntries(extraction).Any())
                throw new InvalidOperationException($"The macOS desktop bundle extracted files under {extraction}.");
        }
    }
}
