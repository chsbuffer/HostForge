using System.Runtime.InteropServices;
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
            $"{RepoContext.GetAvaloniaRidPackageId(Rid)}.{RepoContext.AvaloniaPackageIdentityVersion}.nupkg");
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
}
