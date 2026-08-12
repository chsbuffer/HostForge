using Microsoft.Build.Evaluation;
using Microsoft.Build.Locator;
using System.Runtime.CompilerServices;

namespace HostForge.TestInfra;

static class InitMsBuild {
#pragma warning disable CA2255
    [ModuleInitializer]
#pragma warning restore CA2255
    public static void AssemblyInitialize(){
        MSBuildLocator.RegisterDefaults();
    }
}

public static class RepoContext
{
    public const string AvaloniaAppHostBasePackageId = "ChsBuffer.Avalonia.AppHost";

    public static string RepoRoot { get; } = LocateRepoRoot();

    public static string AvaloniaPackageVersion { get; } = ReadProperty("AvaloniaAppHostPackageVersion");

    public static string TargetAvaloniaVersion { get; } = ReadProperty("TargetAvaloniaVersion");

    public static string AvaloniaPackageIdentityVersion { get; } = StripSemVerBuildMetadata(AvaloniaPackageVersion);

    public static string SkiaSharpVersion { get; } = ReadProperty("SkiaSharpVersion");

    public static string AngleVersion { get; } = ReadProperty("AngleVersion");

    public static string HarfBuzzVersion { get; } = ReadProperty("HarfBuzzVersion");

    public static string HostLibsVersion { get; } = ReadProperty("HostLibsVersion");

    public static string AvaloniaPackageOutputDir { get; } =
        Path.Combine(RepoRoot, "artifacts", "packages", "Release");

    public static string ArtifactsTestRoot { get; } =
        Path.Combine(RepoRoot, "artifacts", "tmp", "build-tests");

    public const string AvaloniaAppHostBuildPackageId = "ChsBuffer.Avalonia.AppHost.Build";

    public static string GetAvaloniaRidPackageId(string rid) => $"{AvaloniaAppHostBasePackageId}.{rid}";

    private static string LocateRepoRoot()
    {
        var current = new DirectoryInfo(AppContext.BaseDirectory);

        while (current is not null)
        {
            if (Directory.Exists(Path.Combine(current.FullName, ".git")))
            {
                return current.FullName;
            }

            current = current.Parent;
        }

        throw new InvalidOperationException("Unable to locate repository root from test process.");
    }

    private static string ReadProperty(string propertyName)
    {
        string propsFile = Path.Combine(RepoRoot, "Directory.Build.props");
        string? value;

        using (var projectCollection = new ProjectCollection())
        {
            var project = new Project(
                propsFile,
                globalProperties: null,
                toolsVersion: null,
                projectCollection: projectCollection,
                loadSettings: ProjectLoadSettings.IgnoreMissingImports);

            value = project.GetPropertyValue(propertyName);
        }

        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException($"Property '{propertyName}' was not found in {propsFile}.");
        }

        return value;
    }

    private static string StripSemVerBuildMetadata(string version)
    {
        int separatorIndex = version.IndexOf('+', StringComparison.Ordinal);
        return separatorIndex < 0 ? version : version[..separatorIndex];
    }
}
