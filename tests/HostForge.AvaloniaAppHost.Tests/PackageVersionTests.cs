using HostForge.TestInfra;
using System.Text.Json;
using TUnit.Core;

namespace HostForge.AvaloniaAppHost.Tests;

public class PackageVersionTests
{
    [Test]
    [Arguments("3.119.4", "", "12.0.0", "8.3.1.5")]
    [Arguments("3.119.4", "beta", "12.0.0-beta", "8.3.1.5")]
    [Arguments("4.153.1", "", "12.0.0-skia4", "14.2.1.301")]
    [Arguments("4.153.1", "beta", "12.0.0-beta.skia4", "14.2.1.301")]
    public async Task PackageVersionsAndDependenciesMatchSelectedSkia(
        string skiaVersion, string preview, string packageVersion, string harfBuzzVersion)
    {
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

    private static void Equal(string actual, string expected)
    {
        if (!string.Equals(actual, expected, StringComparison.Ordinal))
            throw new InvalidOperationException($"Expected '{expected}', got '{actual}'.");
    }
}
