using HostForge.TestInfra;

namespace HostForge.NativeAot.Tests;

public class StaticGraphicsActivationTests
{
    [Test]
    public async Task ManagedVersionValidation_AcceptsReferencesWithoutNuGetPackageId()
    {
        string workspace = Path.Combine(RepoContext.ArtifactsTestRoot, $"static-validation-{Guid.NewGuid():N}");
        string nativeDir = Path.Combine(workspace, "native");
        Directory.CreateDirectory(nativeDir);
        await File.WriteAllTextAsync(Path.Combine(nativeDir, "manifest.json"), "{}");
        string targets = Path.Combine(RepoContext.RepoRoot, "src", "HostForge.StaticGraphics.targets");
        string project = Path.Combine(workspace, "Consumer.proj");
        string skia = RepoContext.SkiaSharpVersion;
        string harfBuzz = RepoContext.GetHarfBuzzVersion(skia);
        string? otherLine = RepoContext.SupportedSkiaSharpVersions.FirstOrDefault(version =>
            RepoContext.VersionPrefix(version, 2) != RepoContext.VersionPrefix(skia, 2));
        if (otherLine is null)
        {
            Skip.Test("A line mismatch requires two supported SkiaSharp lines.");
            return;
        }
        // Project references resolve to ReferencePath items that carry no NuGet package metadata.
        await File.WriteAllTextAsync(project, $"""
            <Project>
              <PropertyGroup><PublishAot>true</PublishAot><RuntimeIdentifier>win-x64</RuntimeIdentifier></PropertyGroup>
              <ItemGroup>
                <HostForgeStaticPackage Include="SkiaSharp"><Component>SkiaSharp</Component><Rid>win-x64</Rid><SkiaLine>{RepoContext.VersionPrefix(skia, 2)}</SkiaLine><HarfBuzzLine>{RepoContext.VersionPrefix(harfBuzz, 3)}</HarfBuzzLine><NativeDir>{System.Security.SecurityElement.Escape(nativeDir)}</NativeDir></HostForgeStaticPackage>
                <ReferencePath Include="SkiaSharp.dll"><NuGetPackageId>SkiaSharp</NuGetPackageId><NuGetPackageVersion>$(ManagedSkiaVersion)</NuGetPackageVersion></ReferencePath>
                <ReferencePath Include="HarfBuzzSharp.dll"><NuGetPackageId>HarfBuzzSharp</NuGetPackageId><NuGetPackageVersion>{harfBuzz}</NuGetPackageVersion></ReferencePath>
                <ReferencePath Include="ProjectReference.dll" />
              </ItemGroup>
              <Target Name="ResolveReferences" />
              <Import Project="{System.Security.SecurityElement.Escape(targets)}" />
            </Project>
            """);

        CommandResult matching = await CommandRunner.RunAsync("dotnet",
            $"msbuild \"{project}\" -t:HostForgeValidateStaticGraphics -p:ManagedSkiaVersion={skia} -v:quiet", workspace);
        AssertEx.Success(matching, "Validation with a project reference in ReferencePath");

        CommandResult mismatched = await CommandRunner.RunAsync("dotnet",
            $"msbuild \"{project}\" -t:HostForgeValidateStaticGraphics -p:ManagedSkiaVersion={otherLine} -v:quiet", workspace);
        if (mismatched.ExitCode == 0)
            throw new InvalidOperationException("A managed SkiaSharp line mismatch was accepted.");
        AssertEx.Contains(mismatched.CombinedOutput, "HFG0002");
    }
}
