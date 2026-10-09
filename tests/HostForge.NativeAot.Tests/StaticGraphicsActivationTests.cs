using System.Text.Json;
using HostForge.TestInfra;

namespace HostForge.NativeAot.Tests;

public class StaticGraphicsActivationTests
{
    [Test]
    public async Task Components_AreSilentOutsideTheirPlatformAndWithoutRid()
    {
        string workspace = Path.Combine(RepoContext.ArtifactsTestRoot, $"static-activation-{Guid.NewGuid():N}");
        Directory.CreateDirectory(workspace);
        string targets = Path.Combine(RepoContext.RepoRoot, "src", "HostForge.StaticGraphics.targets");
        string project = Path.Combine(workspace, "Consumer.proj");
        await File.WriteAllTextAsync(project, $"""
            <Project>
              <PropertyGroup><PublishAot>true</PublishAot><TreatWarningsAsErrors>true</TreatWarningsAsErrors></PropertyGroup>
              <ItemGroup>
                <HostForgeStaticPackage Include="Angle" Condition="'$(Component)' == 'Angle'"><Component>Angle</Component><Rid>win-x64</Rid></HostForgeStaticPackage>
                <HostForgeStaticPackage Include="AvaloniaNative" Condition="'$(Component)' == 'Avalonia.Native'"><Component>Avalonia.Native</Component><Rid>osx-x64</Rid></HostForgeStaticPackage>
              </ItemGroup>
              <Import Project="{System.Security.SecurityElement.Escape(targets)}" />
            </Project>
            """);

        string[] rids = ["", "win-x64", "win-arm64", "linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64", "osx-x64", "osx-arm64"];
        foreach (string component in new[] { "Angle", "Avalonia.Native" })
        {
            foreach (string rid in rids)
            {
                if (rid.StartsWith(component == "Angle" ? "win-" : "osx-", StringComparison.Ordinal))
                    continue;
                CommandResult result = await CommandRunner.RunAsync("dotnet",
                    $"msbuild \"{project}\" -t:HostForgeResolveStaticGraphics -p:Component={component} -p:RuntimeIdentifier={rid} -warnaserror -getItem:NativeLibrary,DirectPInvoke,LinkerArg -v:quiet", workspace);
                AssertEx.Success(result, $"Inactive {component} on RID '{rid}' with warnings as errors");
                using JsonDocument items = JsonDocument.Parse(result.StandardOutput);
                foreach (JsonProperty item in items.RootElement.GetProperty("Items").EnumerateObject())
                {
                    if (item.Value.GetArrayLength() != 0)
                        throw new InvalidOperationException($"{component} injected {item.Name} for RID '{rid}'.");
                }
            }
        }

        foreach ((string component, string rid) in new[] { ("Angle", "win-arm64"), ("Avalonia.Native", "osx-arm64") })
        {
            CommandResult result = await CommandRunner.RunAsync("dotnet",
                $"msbuild \"{project}\" -t:HostForgeResolveStaticGraphics -p:Component={component} -p:RuntimeIdentifier={rid} -warnaserror -v:quiet", workspace);
            if (result.ExitCode == 0)
                throw new InvalidOperationException($"Missing {component} payload for {rid} was accepted with warnings as errors.");
            AssertEx.Contains(result.CombinedOutput, "HFG1001");
        }
    }
}
