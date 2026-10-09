using System.Runtime.InteropServices;
using System.Security;
using System.IO.Compression;
using System.Xml.Linq;
using HostForge.TestInfra;
using TUnit.Core;

namespace HostForge.AvaloniaNative.Tests;

public class AvaloniaNativeTests
{
    [Test]
    [Arguments(true)]
    [Arguments(false)]
    public async Task PublishAssets_AreFilteredBeforeSdkSnapshotsCopyLists(bool aot)
    {
        string root = Path.Combine(RepoContext.ArtifactsTestRoot, $"avalonianative-publish-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        try
        {
            string targets = SecurityElement.Escape(Path.Combine(RepoContext.RepoRoot,
                "src/HostForge.StaticGraphics.targets")) ?? throw new InvalidOperationException();
            string escapedRoot = SecurityElement.Escape(root) ?? throw new InvalidOperationException();
            await File.WriteAllTextAsync(Path.Combine(root, "libAvaloniaNative.a"), "publish contract fixture");
            await File.WriteAllTextAsync(Path.Combine(root, "manifest.json"), "{}");
            string project = Path.Combine(root, "PublishContract.csproj");
            await File.WriteAllTextAsync(project, $$"""
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net10.0</TargetFramework>
                    <PublishAot>false</PublishAot>
                    <RuntimeIdentifier>osx-arm64</RuntimeIdentifier>
                    <PublishDir>{{escapedRoot}}/publish/</PublishDir>
                    <CopyBuildOutputToPublishDirectory>false</CopyBuildOutputToPublishDirectory>
                  </PropertyGroup>
                  <ItemGroup>
                    <PackageReference Include="Avalonia.Desktop" Version="12.1.3" />
                    <HostForgeStaticPackage Include="test" Component="Avalonia.Native" Rid="osx-arm64" AvaloniaVersion="12.1.3" NativeDir="{{escapedRoot}}/" />
                  </ItemGroup>
                  <Import Project="{{targets}}" />
                  <!-- Exercise SDK publish-list processing without cross-OS native compilation. -->
                  <Target Name="SelectPublishMode" BeforeTargets="ComputeResolvedFilesToPublishList">
                    <PropertyGroup>
                      <PublishAot>{{aot.ToString().ToLowerInvariant()}}</PublishAot>
                    </PropertyGroup>
                  </Target>
                  <Target Name="Probe" DependsOnTargets="ResolveReferences;ComputeResolvedFilesToPublishList;_ComputeResolvedFilesToPublishTypes">
                    <ItemGroup>
                      <_NativePublish Include="@(ResolvedFileToPublish)" Condition="'%(Filename)%(Extension)' == 'libAvaloniaNative.dylib'" />
                      <_NativeCopy Include="@(_ResolvedFileToPublishPreserveNewest);@(_ResolvedFileToPublishAlways);@(_ResolvedFileToPublishIfDifferent)"
                                   Condition="'%(Filename)%(Extension)' == 'libAvaloniaNative.dylib'" />
                      <_SkiaPublish Include="@(ResolvedFileToPublish)" Condition="'%(Filename)%(Extension)' == 'libSkiaSharp.dylib'" />
                      <_HarfBuzzPublish Include="@(ResolvedFileToPublish)" Condition="'%(Filename)%(Extension)' == 'libHarfBuzzSharp.dylib'" />
                    </ItemGroup>
                    <Message Importance="high" Text="NATIVE_PUBLISH=@(_NativePublish->Count()) NATIVE_COPY=@(_NativeCopy->Count()) SKIA=@(_SkiaPublish->Count()) HARFBUZZ=@(_HarfBuzzPublish->Count())" />
                  </Target>
                </Project>
                """);
            CommandResult result = await CommandRunner.RunAsync("dotnet", $"msbuild \"{project}\" -restore -t:Probe -v:minimal", root);
            AssertEx.Success(result);
            string count = aot ? "0" : "1";
            AssertEx.Contains(result.CombinedOutput, $"NATIVE_PUBLISH={count} NATIVE_COPY={count} SKIA=1 HARFBUZZ=1");
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Test]
    public async Task Packages_ContainPortableRidRegistrationAndExactMetaDependencies()
    {
        string root = Path.Combine(RepoContext.ArtifactsTestRoot, $"avalonianative pack {Guid.NewGuid():N}");
        string payloads = Path.Combine(root, "payloads");
        string packages = Path.Combine(root, "packages");
        Directory.CreateDirectory(root);
        try
        {
            CommandResult missing = await PackFixtureAsync(root, "osx-arm64");
            await Assert.That(missing.ExitCode).IsNotEqualTo(0);
            AssertEx.Contains(missing.CombinedOutput, "Build the AvaloniaNative payload first");

            foreach (string rid in new[] { "osx-x64", "osx-arm64" })
            {
                await CreatePayloadFixtureAsync(payloads, rid);
                CommandResult pack = await PackFixtureAsync(root, rid);
                AssertEx.Success(pack, $"pack {rid}");

                string id = $"ChsBuffer.Avalonia.Native.Static.{rid}";
                using ZipArchive zip = ZipFile.OpenRead(Path.Combine(packages, $"{id}.12.1.3.1.nupkg"));
                ZipArchiveEntry registration = zip.GetEntry($"buildTransitive/{id}.targets") ?? throw new InvalidOperationException("Missing package registration.");
                using var reader = new StreamReader(registration.Open());
                string xml = await reader.ReadToEndAsync();
                AssertEx.Contains(xml, $"<Rid>{rid}</Rid>");
                AssertEx.Contains(xml, "<AvaloniaVersion>12.1.3</AvaloniaVersion>");
                AssertEx.Contains(xml, "$(MSBuildThisFileDirectory)");
                AssertEx.NotContains(xml, RepoContext.RepoRoot);
                await Assert.That(zip.GetEntry($"build/native/{rid}/libAvaloniaNative.a")).IsNotNull();
                await Assert.That(zip.GetEntry("licenses/licence.md")).IsNotNull();
                await Assert.That(zip.Entries.Any(entry => entry.FullName.StartsWith("runtimes/", StringComparison.Ordinal))).IsFalse();
            }

            CommandResult meta = await PackFixtureAsync(root);
            AssertEx.Success(meta, "pack meta");
            using ZipArchive metaZip = ZipFile.OpenRead(Path.Combine(packages, "ChsBuffer.Avalonia.Native.Static.12.1.3.1.nupkg"));
            ZipArchiveEntry nuspec = metaZip.Entries.Single(entry => entry.FullName.EndsWith(".nuspec", StringComparison.Ordinal));
            using Stream stream = nuspec.Open();
            XDocument document = XDocument.Load(stream);
            XElement[] dependencies = document.Descendants().Where(element => element.Name.LocalName == "dependency").ToArray();
            await Assert.That(dependencies.Length).IsEqualTo(2);
            foreach (XElement dependency in dependencies)
                await Assert.That((string?)dependency.Attribute("version")).IsEqualTo("[12.1.3.1]");
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Test]
    public async Task MetaPackage_AotConsumerBuildsWithoutWarningsOrLinkItemsForOtherPlatforms()
    {
        string root = Path.Combine(RepoContext.ArtifactsTestRoot, $"avalonianative-consumer-{Guid.NewGuid():N}");
        string consumer = Path.Combine(root, "consumer");
        Directory.CreateDirectory(consumer);
        try
        {
            foreach (string rid in new[] { "osx-x64", "osx-arm64" })
            {
                await CreatePayloadFixtureAsync(Path.Combine(root, "payloads"), rid);
                AssertEx.Success(await PackFixtureAsync(root, rid), $"pack {rid}");
            }
            AssertEx.Success(await PackFixtureAsync(root), "pack meta");

            string project = Path.Combine(consumer, "Consumer.csproj");
            string escapedRoot = SecurityElement.Escape(root) ?? throw new InvalidOperationException();
            await File.WriteAllTextAsync(project, $$"""
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net10.0</TargetFramework>
                    <OutputType>Exe</OutputType>
                    <PublishAot>true</PublishAot>
                    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
                    <RestoreAdditionalProjectSources>{{escapedRoot}}/packages</RestoreAdditionalProjectSources>
                    <RestorePackagesPath>{{escapedRoot}}/nuget</RestorePackagesPath>
                  </PropertyGroup>
                  <ItemGroup>
                    <PackageReference Include="ChsBuffer.Avalonia.Native.Static" Version="12.1.3.1" />
                  </ItemGroup>
                  <Target Name="VerifyInactivePackage" AfterTargets="Build">
                    <Error Condition="@(HostForgeStaticPackage->Count()) != 2" Text="Both transitive RID packages must be imported." />
                    <Error Condition="'@(DirectPInvoke)' != '' or '@(NativeLibrary)' != '' or '@(NativeFramework)' != '' or '@(NativeSystemLibrary)' != '' or '@(LinkerArg)' != '' or '$(LinkStandardCPlusPlusLibrary)' == 'true'"
                           Text="AvaloniaNative must not inject linking inputs for RID='$(RuntimeIdentifier)'." />
                    <Message Importance="high" Text="INACTIVE_CONSUMER RID='$(RuntimeIdentifier)' AOT=$(PublishAot)" />
                  </Target>
                </Project>
                """);
            await File.WriteAllTextAsync(Path.Combine(consumer, "Program.cs"), "System.Console.WriteLine(\"Consumer\");");

            // The upstream telemetry collector otherwise outlives the build and locks the temporary NuGet cache.
            var environment = new Dictionary<string, string> { ["AVALONIA_TELEMETRY_OPTOUT"] = "1" };
            foreach (string rid in new[] { "", "win-x64", "linux-x64" })
            {
                string arguments = $"build \"{project}\" -c Release -warnaserror -v:minimal";
                if (rid.Length != 0)
                {
                    CommandResult restore = await CommandRunner.RunAsync("dotnet", $"restore \"{project}\" -r {rid} -warnaserror -v:minimal", RepoContext.RepoRoot, environmentVariables: environment);
                    AssertEx.Success(restore, $"restore {rid}");
                    arguments += $" -r {rid} --no-restore";
                }
                CommandResult build = await CommandRunner.RunAsync("dotnet", arguments, RepoContext.RepoRoot, environmentVariables: environment);
                AssertEx.Success(build, $"build RID='{rid}'");
                AssertEx.NotContains(build.CombinedOutput, "HFG1001");
                AssertEx.Contains(build.CombinedOutput, $"INACTIVE_CONSUMER RID='{rid}' AOT=true");
            }
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    private static async Task CreatePayloadFixtureAsync(string payloads, string rid)
    {
        string payload = Path.Combine(payloads, "12.1.3", rid);
        Directory.CreateDirectory(Path.Combine(payload, "licenses"));
        // Packaging fixtures never enter the normal native artifacts directory.
        await File.WriteAllTextAsync(Path.Combine(payload, "libAvaloniaNative.a"), "package fixture");
        await File.WriteAllTextAsync(Path.Combine(payload, "manifest.json"), "{}");
        await File.WriteAllTextAsync(Path.Combine(payload, "licenses/licence.md"), "license fixture");
    }

    private static Task<CommandResult> PackFixtureAsync(string root, string? rid = null)
    {
        string project = Path.Combine(RepoContext.RepoRoot, rid is null
            ? "src/package-static-graphics/StaticGraphics.csproj"
            : "src/package-avalonia-native-static/Avalonia.Native.Static.Rid.csproj");
        // Isolate generated registration files and NuGet assets across parallel tests.
        string parameters = $"-c Release -p:PackagingRevision=1 -p:AvaloniaNativeArtifactsRoot=\"{root}/payloads\" -p:PackageOutputPath=\"{root}/packages\""
            + $" -p:BaseIntermediateOutputPath=\"{root}/obj/{rid ?? "meta"}/\" -p:RestorePackagesPath=\"{root}/nuget\" -v:minimal";
        parameters += rid is null
            ? $" -p:StaticGraphicsComponent=Avalonia.Native -p:RestoreAdditionalProjectSources=\"{root}/packages\""
            : $" -p:AvaloniaNativeRid={rid}";
        return CommandRunner.RunAsync("dotnet", $"pack \"{project}\" {parameters}", RepoContext.RepoRoot);
    }

    [Test]
    public async Task MacOS_PublishesStaticNativeAndDrawsText()
    {
        if (!OperatingSystem.IsMacOS())
            Skip.Test("AvaloniaNative execution requires macOS.");

        string rid = RuntimeInformation.ProcessArchitecture switch
        {
            Architecture.Arm64 => "osx-arm64",
            Architecture.X64 => "osx-x64",
            _ => throw new PlatformNotSupportedException()
        };
        string project = Path.Combine(RepoContext.RepoRoot, "tests/HostForge.AvaloniaNative.Tests/Smoke/AvaloniaNativeSmoke.csproj");
        string output = Path.Combine(RepoContext.RepoRoot, "artifacts/avalonianative-smoke", rid);
        string packageCache = Path.Combine(RepoContext.RepoRoot, "build", $"avalonianative-nuget-{rid}");
        CommandResult publish = await CommandRunner.RunAsync("dotnet",
            $"publish \"{project}\" -c Release -r {rid} -o \"{output}\" -p:RestorePackagesPath=\"{packageCache}\" -v:minimal", RepoContext.RepoRoot);
        AssertEx.Success(publish, "AvaloniaNative AOT publish");
        AssertEx.FileMissing(Path.Combine(output, "libAvaloniaNative.dylib"));
        AssertEx.FileExists(Path.Combine(output, "libSkiaSharp.dylib"));
        AssertEx.FileExists(Path.Combine(output, "libHarfBuzzSharp.dylib"));

        string executable = Path.Combine(output, "AvaloniaNativeSmoke");
        CommandResult dependencies = await CommandRunner.RunAsync("otool", $"-L \"{executable}\"", output);
        AssertEx.Success(dependencies);
        AssertEx.NotContains(dependencies.CombinedOutput, "libAvaloniaNative");
        // NativeAOT's exported symbol list can localize symbols from static archives.
        CommandResult symbols = await CommandRunner.RunAsync("nm", $"-U \"{executable}\"", output);
        AssertEx.Success(symbols);
        AssertEx.Contains(symbols.CombinedOutput, "_CreateAvaloniaNative");
        await File.WriteAllTextAsync(Path.Combine(output, "linkage.txt"), dependencies.CombinedOutput + symbols.CombinedOutput);

        for (int iteration = 1; iteration <= 5; iteration++)
        {
            string frame = Path.Combine(output, $"run-{iteration}.png");
            File.Delete(frame);
            var environment = new Dictionary<string, string> { ["HOSTFORGE_AVALONIANATIVE_FRAME"] = frame };
            CommandResult run = await CommandRunner.RunAsync(executable, "", output, environmentVariables: environment);
            await File.WriteAllTextAsync(Path.Combine(output, $"run-{iteration}.txt"), run.CombinedOutput);
            AssertEx.Success(run, $"cold process start {iteration}");
            AssertEx.FileExists(frame);
            AssertEx.Contains(run.CombinedOutput, "FONT U+41");
            AssertEx.Contains(run.CombinedOutput, "FONT U+4E2D");
            AssertEx.Contains(run.CombinedOutput, "FONT U+1F600");
            AssertEx.Contains(run.CombinedOutput, "AVN_WINDOW_OPENED");
            AssertEx.Contains(run.CombinedOutput, "AVN_FRAME_RENDERED");
            await Assert.That(run.CombinedOutput.IndexOf("AVN_FRAME_RENDERED", StringComparison.Ordinal)
                < run.CombinedOutput.IndexOf("FONT U+41", StringComparison.Ordinal)).IsTrue();
        }
    }

    [Test]
    [Arguments("12.1.3", "osx-arm64", true, true, "PINVOKE=libAvaloniaNative")]
    [Arguments("12.1.3", "osx-x64", true, true, "PINVOKE=libAvaloniaNative")]
    [Arguments("12.1.0", "osx-arm64", true, true, "HFG0004")]
    [Arguments("12.1.0", "osx-arm64", false, true, "INACTIVE")]
    [Arguments("12.1.3", "linux-x64", true, true, "INACTIVE")]
    [Arguments("12.1.3", "osx-arm64", false, true, "INACTIVE")]
    [Arguments("12.1.3", "osx-arm64", true, false, "HFG1001")]
    [Arguments("12.1.3", "osx-x64", true, false, "HFG1001")]
    public async Task BuildIntegration_UsesResolvedVersionAndExactAotRid(string version, string rid, bool aot, bool hasPayload, string expected)
    {
        string root = Path.Combine(RepoContext.ArtifactsTestRoot, $"avalonianative-contract-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        try
        {
            string targets = SecurityElement.Escape(Path.Combine(RepoContext.RepoRoot,
                "src/HostForge.StaticGraphics.targets")) ?? throw new InvalidOperationException();
            string escapedRoot = SecurityElement.Escape(root) ?? throw new InvalidOperationException();
            // This test exercises MSBuild's contract without invoking a platform linker.
            await File.WriteAllTextAsync(Path.Combine(root, "libAvaloniaNative.a"), "contract fixture");
            await File.WriteAllTextAsync(Path.Combine(root, "manifest.json"), "{}");
            string project = Path.Combine(root, "Contract.csproj");
            await File.WriteAllTextAsync(project, $$"""
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net10.0</TargetFramework>
                    <PublishAot>{{aot.ToString().ToLowerInvariant()}}</PublishAot>
                    <RuntimeIdentifier>{{rid}}</RuntimeIdentifier>
                  </PropertyGroup>
                  <ItemGroup>
                    <PackageReference Include="Avalonia.Native" Version="{{version}}" />
                    <HostForgeStaticPackage Include="test-arm64" Component="Avalonia.Native" Rid="osx-arm64" AvaloniaVersion="12.1.3" NativeDir="{{escapedRoot}}/"
                                                   Condition="'{{hasPayload}}' == 'true' or '{{rid}}' != 'osx-arm64'" />
                    <HostForgeStaticPackage Include="test-x64" Component="Avalonia.Native" Rid="osx-x64" AvaloniaVersion="12.1.3" NativeDir="{{escapedRoot}}/"
                                                   Condition="'{{hasPayload}}' == 'true' or '{{rid}}' != 'osx-x64'" />
                    <ResolvedFileToPublish Include="libAvaloniaNative.dylib;libSkiaSharp.dylib;libHarfBuzzSharp.dylib" />
                  </ItemGroup>
                  <Import Project="{{targets}}" />
                  <Target Name="SeedLinkerArguments" DependsOnTargets="HostForgeValidateStaticGraphics">
                    <ItemGroup>
                      <LinkerArg Include="@(NativeLibrary)" />
                    </ItemGroup>
                  </Target>
                  <Target Name="Probe" DependsOnTargets="SeedLinkerArguments;HostForgeForceLoadAvaloniaNative;HostForgeFilterStaticGraphicsRuntimeCopies">
                    <Message Importance="high" Text="PINVOKE=@(DirectPInvoke)" />
                    <Message Importance="high" Text="PUBLISH=@(ResolvedFileToPublish)" />
                    <Message Importance="high" Text="LINK=%(LinkerArg.Identity)" Condition="'@(LinkerArg)' != ''" />
                    <Message Importance="high" Text="ARCHIVES=@(NativeLibrary->Count())" />
                    <Message Importance="high" Text="INACTIVE" Condition="'@(NativeLibrary)' == '' and '@(DirectPInvoke)' == ''" />
                  </Target>
                </Project>
                """);
            CommandResult result = await CommandRunner.RunAsync("dotnet", $"msbuild \"{project}\" -restore -t:Probe -v:minimal", root);
            if (expected == "HFG0004")
                await Assert.That(result.ExitCode).IsNotEqualTo(0);
            else
                AssertEx.Success(result);
            AssertEx.Contains(result.CombinedOutput, expected);
            if (expected == "INACTIVE")
                AssertEx.NotContains(result.CombinedOutput, "HFG1001");
            if (!aot || rid == "linux-x64" || !hasPayload)
            {
                AssertEx.NotContains(result.CombinedOutput, "PINVOKE=libAvaloniaNative");
                AssertEx.Contains(result.CombinedOutput, "PUBLISH=libAvaloniaNative.dylib;libSkiaSharp.dylib;libHarfBuzzSharp.dylib");
            }
            if (expected.StartsWith("PINVOKE=", StringComparison.Ordinal))
            {
                AssertEx.Contains(result.CombinedOutput, "PUBLISH=libSkiaSharp.dylib;libHarfBuzzSharp.dylib");
                AssertEx.Contains(result.CombinedOutput, "LINK=-Wl,-force_load,");
                AssertEx.NotContains(result.CombinedOutput, $"LINK={Path.Combine(root, "libAvaloniaNative.a")}");
                AssertEx.Contains(result.CombinedOutput, "ARCHIVES=1");
            }
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }
}
