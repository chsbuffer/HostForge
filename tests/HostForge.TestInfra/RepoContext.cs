using Microsoft.Build.Evaluation;
using Microsoft.Build.Locator;
using System.Runtime.CompilerServices;
using System.Text.Json;

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

    public static string RepoRoot { get; } = ReadRepoRoot();

    public static string AvaloniaPackageVersion { get; } = ReadProperty("AvaloniaAppHostPackageVersion");

    public static string TargetAvaloniaVersion { get; } = ReadProperty("TargetAvaloniaVersion");

    public static string AvaloniaPackageIdentityVersion { get; } = StripSemVerBuildMetadata(AvaloniaPackageVersion);

    public static string SkiaSharpVersion { get; } = ReadProperty("SkiaSharpVersion");

    public static string SkiaSharpPackageVersion { get; } = ReadProperty("SkiaSharpPackageVersion");

    public static string AnglePackageVersion { get; } = ReadProperty("AnglePackageVersion");

    public static string AvaloniaNativeVersion { get; } = ReadProperty("AvaloniaNativeVersion");

    public static string AvaloniaNativePackageVersion { get; } = ReadProperty("AvaloniaNativePackageVersion");

    public static string GetSkiaSharpPackageVersion(string upstreamVersion) =>
        ReadProperty("SkiaSharpPackageVersion", new Dictionary<string, string> { ["SkiaSharpVersion"] = upstreamVersion });

    public static string AngleVersion { get; } = ReadProperty("AngleVersion");

    public static string HarfBuzzVersion { get; } = ReadProperty("HarfBuzzVersion");

    private static readonly IReadOnlyList<KeyValuePair<string, string>> SkiaSharpSources = ReadSkiaSharpSources();

    /// <summary>Supported SkiaSharp versions, in the order of native/skiasharp/versions.json.</summary>
    public static IReadOnlyList<string> SupportedSkiaSharpVersions { get; } = SkiaSharpSources.Select(source => source.Key).ToArray();

    /// <summary>The HarfBuzzSharp version that native/skiasharp/versions.json pairs with a SkiaSharp version.</summary>
    public static string GetHarfBuzzVersion(string skiaSharpVersion) =>
        SkiaSharpSources.FirstOrDefault(source => source.Key == skiaSharpVersion).Value
        ?? throw new ArgumentException($"SkiaSharp {skiaSharpVersion} is not in native/skiasharp/versions.json.", nameof(skiaSharpVersion));

    /// <summary>The first <paramref name="components"/> numbers of a version, e.g. the native line of a SkiaSharp (2) or HarfBuzzSharp (3) version.</summary>
    public static string VersionPrefix(string version, int components) =>
        string.Join('.', version.Split('.').Take(components));

    public static string HostLibsVersion { get; } = ReadProperty("HostLibsVersion");

    /// <summary>The .NET runtime version of the host libraries linked into the templates for a target framework.</summary>
    public static string GetHostLibsVersion(string targetFramework) =>
        ReadProperty("HostLibsVersion", new Dictionary<string, string> { ["HostLibsTargetFramework"] = targetFramework });

    /// <summary>Target frameworks with host libraries, Avalonia templates and test coverage.</summary>
    public static IReadOnlyList<string> TargetFrameworks { get; } = ReadList("HostForgeTargetFrameworks");

    public static IReadOnlyList<string> AvaloniaTemplateTfms { get; } = ReadList("AvaloniaTemplateTfms");

    /// <summary>Data source for tests that run once per supported target framework.</summary>
    public static IEnumerable<Func<string>> TargetFrameworkCases() =>
        TargetFrameworks.Select(targetFramework => (Func<string>)(() => targetFramework));

    public static IEnumerable<Func<string>> AvaloniaTemplateTfmCases() =>
        AvaloniaTemplateTfms.Select(targetFramework => (Func<string>)(() => targetFramework));

    /// <summary>
    /// Data source for single-file publish tests: every template TFM, without and with
    /// <c>IncludeNativeLibrariesForSelfExtract</c>, which moves native libraries from the publish directory
    /// into the bundle and extracts them at startup.
    /// </summary>
    public static IEnumerable<Func<(string TargetFramework, bool IncludeNativeLibrariesForSelfExtract)>> AvaloniaSingleFilePublishCases() =>
        from targetFramework in AvaloniaTemplateTfms
        from includeNativeLibrariesForSelfExtract in new[] { false, true }
        select (Func<(string, bool)>)(() => (targetFramework, includeNativeLibrariesForSelfExtract));

    public static string AvaloniaPackageOutputDir { get; } =
        Path.Combine(RepoRoot, "artifacts", "packages", "Release");

    public static string ArtifactsTestRoot { get; } =
        Path.Combine(RepoRoot, "artifacts", "tmp", "build-tests");

    public const string AvaloniaAppHostBuildPackageId = "ChsBuffer.Avalonia.AppHost.Build";

    public static string GetAvaloniaRidPackageId(string rid) => $"{AvaloniaAppHostBasePackageId}.{rid}";

    private static string ReadRepoRoot()
    {
        string? root = AppContext.GetData("HostForgeRepoRoot") as string;
        if (string.IsNullOrWhiteSpace(root))
            throw new InvalidOperationException("The build did not provide HostForgeRepoRoot.");

        return Path.GetFullPath(root);
    }

    private static string ReadProperty(string propertyName, IDictionary<string, string>? globalProperties = null)
    {
        string propsFile = Path.Combine(RepoRoot, "Directory.Build.props");
        string? value;

        using (var projectCollection = new ProjectCollection())
        {
            var project = new Project(
                propsFile,
                globalProperties: globalProperties,
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

    private static KeyValuePair<string, string>[] ReadSkiaSharpSources()
    {
        // Each entry maps a SkiaSharp version to [mono/skia commit, HarfBuzzSharp version].
        string path = Path.Combine(RepoRoot, "native", "skiasharp", "versions.json");
        using JsonDocument document = JsonDocument.Parse(File.ReadAllText(path));
        return document.RootElement.EnumerateObject()
            .Select(entry => KeyValuePair.Create(entry.Name, entry.Value[1].GetString()
                ?? throw new InvalidOperationException($"{path}: missing HarfBuzzSharp version of {entry.Name}.")))
            .ToArray();
    }

    private static string[] ReadList(string propertyName) =>
        ReadProperty(propertyName).Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    private static string StripSemVerBuildMetadata(string version)
    {
        int separatorIndex = version.IndexOf('+', StringComparison.Ordinal);
        return separatorIndex < 0 ? version : version[..separatorIndex];
    }
}
