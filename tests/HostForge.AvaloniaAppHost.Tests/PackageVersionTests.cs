using HostForge.TestInfra;
using System.Text.Json;
using TUnit.Core;

namespace HostForge.AvaloniaAppHost.Tests;

public class PackageVersionTests
{
    public static IEnumerable<Func<(string SkiaVersion, string Preview)>> SkiaAndPreviewCases() =>
        from skiaVersion in RepoContext.SupportedSkiaSharpVersions
        from preview in new[] { "", "beta" }
        select (Func<(string, string)>)(() => (skiaVersion, preview));

    [Test]
    [MethodDataSource(nameof(SkiaAndPreviewCases))]
    public async Task PackageVersionsAndDependenciesMatchSelectedSkia(string skiaVersion, string preview)
    {
        // Directory.Build.props labels packages built for the SkiaSharp 4 line with skia4.
        string[] labels = new[] { preview, Version.Parse(skiaVersion).Major == 4 ? "skia4" : "" }
            .Where(label => label.Length != 0).ToArray();
        string packageVersion = labels.Length == 0 ? "12.0.0" : $"12.0.0-{string.Join('.', labels)}";
        string harfBuzzVersion = RepoContext.GetHarfBuzzVersion(skiaVersion);
        string directory = Path.Combine(RepoContext.RepoRoot, "src", "package-avalonia-apphost");
        foreach (string filename in new[]
                 {
                     "AvaloniaAppHost.Link.proj", "AvaloniaAppHost.Rid.csproj",
                     "AvaloniaAppHost.Build.csproj", "AvaloniaAppHost.csproj"
                 })
        {
            CommandResult result = await CommandRunner.RunAsync("dotnet",
                $"msbuild \"{Path.Combine(directory, filename)}\" -nologo "
                + $"-p:SkiaSharpVersion={skiaVersion} -p:_Preview={preview} "
                + "-p:TargetAvaloniaVersion=12.0 -p:_Major=0 -p:AvaloniaHostRid=win-x64 "
                + "-getProperty:AvaloniaAppHostPackageVersion,_Preview,AvaloniaAppHostArtifactRoot -getItem:PackageReference",
                RepoContext.RepoRoot);
            AssertEx.Success(result, filename);
            using JsonDocument document = JsonDocument.Parse(result.StandardOutput);
            JsonElement properties = document.RootElement.GetProperty("Properties");
            Equal(properties.GetProperty("AvaloniaAppHostPackageVersion").GetString()!, packageVersion);
            Equal(properties.GetProperty("_Preview").GetString()!, preview);
            foreach (JsonElement reference in document.RootElement.GetProperty("Items").GetProperty("PackageReference").EnumerateArray())
            {
                string packageId = reference.GetProperty("Identity").GetString()!;
                string? expected = packageId switch
                {
                    "SkiaSharp" => skiaVersion,
                    "HarfBuzzSharp" => harfBuzzVersion,
                    _ when packageId.StartsWith("ChsBuffer.Avalonia.AppHost.", StringComparison.Ordinal)
                        => packageVersion,
                    _ => null
                };
                if (expected is not null)
                    Equal(reference.GetProperty("Version").GetString()!, expected);
            }
            if (filename is "AvaloniaAppHost.Link.proj" or "AvaloniaAppHost.Rid.csproj")
                Equal(properties.GetProperty("AvaloniaAppHostArtifactRoot").GetString()!,
                    Path.Combine(RepoContext.RepoRoot, "artifacts", "avalonia-host", "12.0"));
        }
    }

    public static IEnumerable<Func<(string SkiaVersion, string Rid)>> SkiaAndRidCases() =>
        from skiaVersion in RepoContext.SupportedSkiaSharpVersions
        from rid in new[] { "win-x64", "win-arm64", "linux-x64", "linux-musl-x64", "linux-musl-arm64" }
        select (Func<(string, string)>)(() => (skiaVersion, rid));

    // SkiaSharp and HarfBuzzSharp depend on their Windows and macOS native assets but not on the Linux ones, and
    // builds that no template covers load the dynamic libraries.
    [Test]
    [MethodDataSource(nameof(SkiaAndRidCases))]
    public async Task LinuxRidPackagesDependOnTheLinuxNativeAssetsOfTheManagedVersions(string skiaVersion, string rid)
    {
        string harfBuzzVersion = RepoContext.GetHarfBuzzVersion(skiaVersion);
        string project = Path.Combine(RepoContext.RepoRoot, "src", "package-avalonia-apphost", "AvaloniaAppHost.Rid.csproj");
        CommandResult result = await CommandRunner.RunAsync("dotnet",
            $"msbuild \"{project}\" -nologo -p:SkiaSharpVersion={skiaVersion} -p:AvaloniaHostRid={rid} -getItem:PackageReference",
            RepoContext.RepoRoot);
        AssertEx.Success(result, $"AvaloniaAppHost.Rid.csproj ({rid}, {skiaVersion})");
        using JsonDocument document = JsonDocument.Parse(result.StandardOutput);
        IEnumerable<string> actual = document.RootElement.GetProperty("Items").GetProperty("PackageReference").EnumerateArray()
            .Select(reference => $"{reference.GetProperty("Identity").GetString()} {reference.GetProperty("Version").GetString()}")
            .Where(reference => reference.Contains(".NativeAssets.", StringComparison.Ordinal));
        string[] expected = rid.StartsWith("linux-", StringComparison.Ordinal)
            ? [$"HarfBuzzSharp.NativeAssets.Linux {harfBuzzVersion}", $"SkiaSharp.NativeAssets.Linux {skiaVersion}"]
            : [];
        Equal(string.Join(", ", actual.Order(StringComparer.Ordinal)), string.Join(", ", expected));
    }

    private static void Equal(string actual, string expected)
    {
        if (!string.Equals(actual, expected, StringComparison.Ordinal))
            throw new InvalidOperationException($"Expected '{expected}', got '{actual}'.");
    }
}
