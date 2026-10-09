using System.Buffers.Binary;
using System.Reflection.PortableExecutable;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.RegularExpressions;
using HostForge.TestInfra;

namespace HostForge.NativeAot.Tests;

public class NativeAotMatrixTests
{
    private static string SkiaVersion => Environment.GetEnvironmentVariable("HOSTFORGE_SKIA_VERSION") ?? RepoContext.SkiaSharpVersion;
    private static bool CheckAngle => Environment.GetEnvironmentVariable("HOSTFORGE_CHECK_ANGLE") != "false";
    internal static string Rid => Environment.GetEnvironmentVariable("HOSTFORGE_NATIVE_RID") ?? RuntimeInformation.RuntimeIdentifier;
    private static bool IsArm64 => Rid.EndsWith("-arm64", StringComparison.Ordinal);
    private static bool IsMusl => Rid.StartsWith("linux-musl-", StringComparison.Ordinal);
    private static Architecture TargetArchitecture => IsArm64 ? Architecture.Arm64 : Architecture.X64;

    [Test]
    public async Task TargetRid_PublishesAndRunsWithStaticGraphics()
    {
        if (!OperatingSystem.IsWindows() && !OperatingSystem.IsLinux() && !OperatingSystem.IsMacOS())
            throw new PlatformNotSupportedException("This matrix requires Windows, Linux or macOS.");

        await PublishAndRunAsync(useMeta: false);
        if (Environment.GetEnvironmentVariable("HOSTFORGE_TEST_META") == "true")
            await PublishAndRunAsync(useMeta: true);
    }

    private static async Task PublishAndRunAsync(bool useMeta)
    {
        string workspace = CreateWorkspace(useMeta ? "meta" : "rid");
        string output = Path.Combine(workspace, "publish");
        string properties = Properties(workspace) + $" -p:UseStaticGraphicsMeta={useMeta.ToString().ToLowerInvariant()}";
        string arguments = $"publish NativeAotMatrix.csproj -c Release -r {Rid} {properties} -p:PublishDir=\"{output}\" -bl:\"{Path.Combine(workspace, "publish.binlog")}\" -warnaserror -v:minimal";
        CommandResult publish = await CommandRunner.RunAsync("dotnet", arguments, workspace);
        await File.WriteAllTextAsync(Path.Combine(workspace, "publish.log"), publish.CombinedOutput);
        AssertEx.Success(publish, $"NativeAOT publish ({Rid}, {SkiaVersion}, meta={useMeta})");

        using (JsonDocument assets = JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(workspace, "obj", "project.assets.json"))))
        {
            JsonElement libraries = assets.RootElement.GetProperty("libraries");
            string expectedHarfBuzz = SkiaVersion == "3.119.4" ? "8.3.1.5" : "14.2.1.301";
            if (!libraries.TryGetProperty($"SkiaSharp/{SkiaVersion}", out _) || !libraries.TryGetProperty($"HarfBuzzSharp/{expectedHarfBuzz}", out _))
                throw new InvalidOperationException("The static package did not raise the transitive managed graphics versions.");
        }

        string executable = Path.Combine(output, OperatingSystem.IsWindows() ? "NativeAotMatrix.exe" : "NativeAotMatrix");
        AssertEx.FileExists(executable);
        VerifyArchitecture(executable);
        string[] forbidden = ["libSkiaSharp.dll", "libHarfBuzzSharp.dll", "libSkiaSharp.so", "libHarfBuzzSharp.so", "libSkiaSharp.dylib", "libHarfBuzzSharp.dylib"];
        foreach (string file in Directory.EnumerateFiles(output, "*", SearchOption.AllDirectories))
        {
            string name = Path.GetFileName(file);
            if (forbidden.Contains(name, StringComparer.OrdinalIgnoreCase) ||
                ((useMeta || CheckAngle) && name.Equals("av_libglesv2.dll", StringComparison.OrdinalIgnoreCase)) ||
                (useMeta && name.Equals("libAvaloniaNative.dylib", StringComparison.OrdinalIgnoreCase)))
                throw new InvalidOperationException($"A dynamic graphics library was published: {file}");
        }

        string? runner = Environment.GetEnvironmentVariable("HOSTFORGE_NATIVE_RUNNER");
        CommandResult run = runner is { Length: > 0 }
            ? await CommandRunner.RunAsync(runner, $"\"{executable}\"", output)
            : OperatingSystem.IsMacOS()
                ? await CommandRunner.RunAsync("/usr/bin/env", $"DYLD_PRINT_LIBRARIES=1 \"{executable}\"", output)
                : await CommandRunner.RunAsync(executable, string.Empty, output);
        await File.WriteAllTextAsync(Path.Combine(workspace, "run.log"), run.CombinedOutput);
        AssertEx.Success(run, $"NativeAOT run ({Rid}, {SkiaVersion}, meta={useMeta})");
        AssertEx.Contains(run.CombinedOutput, SkiaVersion == "3.119.4" ? "SkiaNative=119." : "SkiaNative=153.");
        AssertEx.Contains(run.CombinedOutput, $"Architecture={TargetArchitecture}");
        AssertEx.Contains(run.CombinedOutput, "SoftwarePixels=1024/1024");
        AssertEx.Contains(run.CombinedOutput, "HarfBuzzShape=OK");
        if (OperatingSystem.IsWindows() && CheckAngle)
            AssertEx.Contains(run.CombinedOutput, "AngleError=0x");

        if (OperatingSystem.IsLinux())
        {
            await AuditElfAsync(executable, workspace);
            if (!IsMusl && Environment.GetEnvironmentVariable("ROOTFS_DIR") is { Length: > 0 })
                await AuditElfVersionsAsync(executable, workspace);
        }
        if (OperatingSystem.IsMacOS())
        {
            AssertEx.Contains(run.StandardOutput, "AppKitFontWeights=OK");
            if (Regex.IsMatch(run.StandardError, @"lib(SkiaSharp|HarfBuzzSharp)\.dylib", RegexOptions.IgnoreCase))
                throw new InvalidOperationException("The process loaded a dynamic graphics library.");
            await AuditMachOAsync(executable, workspace);
        }
        await File.WriteAllTextAsync(Path.Combine(workspace, "result.json"), JsonSerializer.Serialize(new
        {
            rid = Rid, skiaSharp = SkiaVersion, architecture = TargetArchitecture.ToString(),
            meta = useMeta, exitCode = run.ExitCode, softwarePixels = 1024, harfBuzzShape = "OK",
            dynamicGraphicsLibraries = 0, machOAudit = OperatingSystem.IsMacOS()
        }, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine($"PASS {Rid} Skia={SkiaVersion} meta={useMeta}: {workspace}\n{run.CombinedOutput}");
    }

    [Test]
    public async Task ManagedNativeVersionMismatch_IsRejectedBeforeLinking()
    {
        // A higher incompatible direct dependency is valid to NuGet but unsafe to link.
        if (SkiaVersion != "3.119.4")
            return;
        foreach ((string property, string version, string code) in new[]
        {
            ("MismatchedSkiaSharpVersion", "4.153.1", "HFG0002"),
            ("MismatchedHarfBuzzVersion", "14.2.1.301", "HFG0003")
        })
        {
            string workspace = CreateWorkspace(code);
            string arguments = $"msbuild NativeAotMatrix.csproj -restore -t:HostForgeValidateStaticGraphics {Properties(workspace)} -p:{property}={version} -v:minimal";
            CommandResult result = await CommandRunner.RunAsync("dotnet", arguments, workspace);
            await File.WriteAllTextAsync(Path.Combine(workspace, "guard.log"), result.CombinedOutput);
            if (result.ExitCode == 0)
                throw new InvalidOperationException($"An incompatible {property} was accepted.");
            AssertEx.Contains(result.CombinedOutput, code);
        }
    }

    [Test]
    public async Task MultiRidMetaConsumer_BuildsWithoutRidWithWarningsAsErrors()
    {
        if (Environment.GetEnvironmentVariable("HOSTFORGE_TEST_META") != "true")
            return;

        string workspace = CreateWorkspace("no-rid");
        string properties = Properties(workspace, includeRid: false) + " -p:UseStaticGraphicsMeta=true";
        CommandResult build = await CommandRunner.RunAsync("dotnet", $"build NativeAotMatrix.csproj {properties} -warnaserror -v:minimal", workspace);
        await File.WriteAllTextAsync(Path.Combine(workspace, "build.log"), build.CombinedOutput);
        AssertEx.Success(build, "Multi-RID consumer build without -r");
        CommandResult resolve = await CommandRunner.RunAsync("dotnet",
            $"msbuild NativeAotMatrix.csproj {properties} -t:HostForgeResolveStaticGraphics -getItem:NativeLibrary,DirectPInvoke -v:quiet", workspace);
        AssertEx.Success(resolve, "Static input evaluation without RID");
        using JsonDocument items = JsonDocument.Parse(resolve.StandardOutput);
        foreach (JsonProperty item in items.RootElement.GetProperty("Items").EnumerateObject())
        {
            if (item.Value.GetArrayLength() != 0)
                throw new InvalidOperationException($"{item.Name} was injected without a RID.");
        }
    }

    [Test]
    public async Task NonAotConsumer_DoesNotActivateStaticInputs()
    {
        string workspace = CreateWorkspace("non-aot");
        CommandResult result = await CommandRunner.RunAsync("dotnet",
            $"msbuild NativeAotMatrix.csproj -restore -t:HostForgeResolveStaticGraphics {Properties(workspace)} -p:PublishAot=false -getItem:NativeLibrary,DirectPInvoke -v:quiet", workspace);
        AssertEx.Success(result, "Non-AOT package evaluation");
        int jsonStart = result.StandardOutput.IndexOf('{');
        using JsonDocument items = JsonDocument.Parse(result.StandardOutput.Substring(jsonStart));
        if (items.RootElement.GetProperty("Items").GetProperty("NativeLibrary").GetArrayLength() != 0 ||
            items.RootElement.GetProperty("Items").GetProperty("DirectPInvoke").GetArrayLength() != 0)
            throw new InvalidOperationException("Static graphics inputs leaked into a non-AOT consumer.");
    }

    private static string CreateWorkspace(string purpose)
    {
        string workspace = Path.Combine(RepoContext.ArtifactsTestRoot, $"nativeaot-{Rid}-{SkiaVersion}-{purpose}-{Guid.NewGuid():N}");
        Directory.CreateDirectory(workspace);
        string sample = Path.Combine(RepoContext.RepoRoot, "samples", "nativeaot-matrix");
        File.Copy(Path.Combine(sample, "NativeAotMatrix.csproj"), Path.Combine(workspace, "NativeAotMatrix.csproj"));
        File.Copy(Path.Combine(sample, "Program.cs"), Path.Combine(workspace, "Program.cs"));
        return workspace;
    }

    [Test]
    public async Task MultipleNativeLines_AreRejectedAndMissingRidWarns()
    {
        string workspace = CreateWorkspace("native-lines");
        string targets = Path.Combine(RepoContext.RepoRoot, "src", "HostForge.StaticGraphics.targets");
        string project = Path.Combine(workspace, "Guard.proj");
        await File.WriteAllTextAsync(project, $"""
            <Project>
              <PropertyGroup><PublishAot>true</PublishAot><RuntimeIdentifier>win-x64</RuntimeIdentifier></PropertyGroup>
              <ItemGroup>
                <HostForgeStaticPackage Include="Skia3"><Component>SkiaSharp</Component><Rid>win-x64</Rid><SkiaLine>3.119</SkiaLine></HostForgeStaticPackage>
                <HostForgeStaticPackage Include="Skia4"><Component>SkiaSharp</Component><Rid>win-x64</Rid><SkiaLine>4.153</SkiaLine></HostForgeStaticPackage>
              </ItemGroup>
              <Import Project="{System.Security.SecurityElement.Escape(targets)}" />
            </Project>
            """);
        CommandResult conflict = await CommandRunner.RunAsync("dotnet", $"msbuild \"{project}\" -t:HostForgeResolveStaticGraphics -v:minimal", workspace);
        if (conflict.ExitCode == 0)
            throw new InvalidOperationException("Conflicting native lines were accepted.");
        AssertEx.Contains(conflict.CombinedOutput, "HFG0001");

        CommandResult missingRid = await CommandRunner.RunAsync("dotnet", $"msbuild \"{project}\" -t:HostForgeResolveStaticGraphics -p:RuntimeIdentifier=win-arm64 -v:minimal", workspace);
        AssertEx.Success(missingRid, "Unsupported RID warning");
        AssertEx.Contains(missingRid.CombinedOutput, "HFG1001");
        foreach (string inactiveRid in new[] { "", "android-arm64", "browser-wasm" })
        {
            CommandResult inactive = await CommandRunner.RunAsync("dotnet", $"msbuild \"{project}\" -t:HostForgeResolveStaticGraphics -p:RuntimeIdentifier={inactiveRid} -v:minimal", workspace);
            AssertEx.Success(inactive, "A RID outside the package platform stays silent");
            if (inactive.CombinedOutput.Contains("HFG1001", StringComparison.Ordinal))
                throw new InvalidOperationException($"An inactive package warned for RID '{inactiveRid}'.");
        }
    }

    private static string Properties(string workspace, bool includeRid = true)
    {
        string properties = $"-p:Configuration=Release -p:SkiaSharpVersion={SkiaVersion} -p:CheckAngle={CheckAngle.ToString().ToLowerInvariant()} -p:RestorePackagesPath=\"{Path.Combine(workspace, "obj", "packages")}\"";
        if (includeRid)
            properties += $" -p:RuntimeIdentifier={Rid} -p:NativeAotMatrixRid={Rid}";
        if (OperatingSystem.IsLinux() && Environment.GetEnvironmentVariable("ROOTFS_DIR") is { Length: > 0 } sysroot)
            properties += $" -p:SysRoot=\"{sysroot}\" -p:CppCompilerAndLinker=clang -p:LinkStandardCPlusPlusLibrary=true";
        if (IsMusl)
            properties += $" -p:LinkerFlavor=lld -p:TargetTriple={(IsArm64 ? "aarch64" : "x86_64")}-alpine-linux-musl";
        return properties;
    }

    private static void VerifyArchitecture(string executable)
    {
        using FileStream stream = File.OpenRead(executable);
        if (OperatingSystem.IsWindows())
        {
            using var pe = new PEReader(stream);
            if (pe.PEHeaders.CoffHeader.Machine != (IsArm64 ? Machine.Arm64 : Machine.Amd64))
                throw new InvalidOperationException($"The NativeAOT executable is not {TargetArchitecture} PE.");
        }
        else if (OperatingSystem.IsMacOS())
        {
            Span<byte> header = stackalloc byte[8];
            stream.ReadExactly(header);
            uint cpu = TargetArchitecture == Architecture.Arm64 ? 0x0100000cu : 0x01000007u;
            if (BinaryPrimitives.ReadUInt32LittleEndian(header) != 0xfeedfacfu || BinaryPrimitives.ReadUInt32LittleEndian(header.Slice(4)) != cpu)
                throw new InvalidOperationException($"The NativeAOT executable is not {TargetArchitecture} Mach-O.");
        }
        else
        {
            Span<byte> header = stackalloc byte[20];
            stream.ReadExactly(header);
            if (!header.Slice(0, 4).SequenceEqual(new byte[] { 0x7f, (byte)'E', (byte)'L', (byte)'F' }) || header[4] != 2 || header[5] != 1 || header[18] != (IsArm64 ? 183 : 62) || header[19] != 0)
                throw new InvalidOperationException($"The NativeAOT executable is not a matching ELF64 for {Rid}.");
        }
    }

    private static async Task AuditElfAsync(string executable, string workspace)
    {
        CommandResult readelf = await CommandRunner.RunAsync("readelf", $"-h -l -d -V \"{executable}\"", workspace);
        AssertEx.Success(readelf, "ELF architecture, interpreter and dependency audit");
        await File.WriteAllTextAsync(Path.Combine(workspace, "elf.txt"), readelf.StandardOutput);
        if (IsMusl)
        {
            AssertEx.Contains(readelf.StandardOutput, IsArm64 ? "/lib/ld-musl-aarch64.so.1" : "/lib/ld-musl-x86_64.so.1");
            if (Regex.IsMatch(readelf.StandardOutput, @"lib(c|m)\.so\.6|lib(pthread|dl|rt)\.so\.[012]|ld-linux"))
                throw new InvalidOperationException("The musl executable depends on glibc.");
            string provider = string.Empty;
            foreach (string line in readelf.StandardOutput.Split('\n'))
            {
                Match file = Regex.Match(line, @"\bFile: (\S+)");
                if (file.Success)
                    provider = file.Groups[1].Value;
                Match version = Regex.Match(line, @"\bName: (GLIBC_\S+)");
                // GCC's libgcc-glibc.ver also labels musl unwind exports GLIBC_2.0.
                if (version.Success && (provider != "libgcc_s.so.1" || version.Groups[1].Value != "GLIBC_2.0"))
                    throw new InvalidOperationException($"The musl executable requires {version.Groups[1].Value} from {provider}.");
            }
        }
        else
        {
            AssertEx.Contains(readelf.StandardOutput, IsArm64 ? "/lib/ld-linux-aarch64.so.1" : "/lib64/ld-linux-x86-64.so.2");
        }
    }

    private static async Task AuditMachOAsync(string executable, string workspace)
    {
        string nativeRoot = Path.Combine(workspace, "obj", "packages", $"chsbuffer.skiasharp.static.{Rid}", SkiaVersion, "build", "native", Rid);
        string[] archives = Directory.GetFiles(nativeRoot, "*.a");
        if (archives.Length == 0)
            throw new InvalidOperationException("No macOS static archives were restored.");
        string expectedArch = TargetArchitecture == Architecture.Arm64 ? "arm64" : "x86_64";
        Version deploymentTarget = TargetArchitecture == Architecture.Arm64 ? new Version(11, 0) : new Version(10, 13);
        foreach (string file in archives.Append(executable))
        {
            CommandResult lipo = await CommandRunner.RunAsync("lipo", $"-info \"{file}\"", workspace);
            AssertEx.Success(lipo, "Mach-O architecture audit");
            if (!lipo.StandardOutput.TrimEnd().EndsWith($"architecture: {expectedArch}", StringComparison.Ordinal))
                throw new InvalidOperationException($"Unexpected Mach-O architecture: {lipo.StandardOutput}");
            CommandResult otool = await CommandRunner.RunAsync("otool", $"-l \"{file}\"", workspace);
            AssertEx.Success(otool, "Mach-O deployment target audit");
            await File.WriteAllTextAsync(Path.Combine(workspace, Path.GetFileName(file) + ".macho.txt"), lipo.CombinedOutput + otool.CombinedOutput);
            MatchCollection versions = Regex.Matches(otool.StandardOutput, @"cmd LC_(?:BUILD_VERSION|VERSION_MIN_MACOSX)\s+cmdsize \d+\s+(?:platform \S+\s+minos|version) (\d+\.\d+(?:\.\d+)?)");
            if (versions.Count == 0)
                throw new InvalidOperationException($"No macOS deployment target found in {file}.");
            foreach (Match match in versions)
            {
                Version version = Version.Parse(match.Groups[1].Value);
                if (file != executable && (version.Major != deploymentTarget.Major || version.Minor != deploymentTarget.Minor))
                    throw new InvalidOperationException($"{file} requires macOS {version}, expected {deploymentTarget}.");
            }
        }
        CommandResult dependencies = await CommandRunner.RunAsync("otool", $"-L \"{executable}\"", workspace);
        AssertEx.Success(dependencies, "Mach-O dynamic dependency audit");
        await File.WriteAllTextAsync(Path.Combine(workspace, "dependencies.macho.txt"), dependencies.CombinedOutput);
        if (Regex.IsMatch(dependencies.StandardOutput, @"lib(SkiaSharp|HarfBuzzSharp)\.dylib", RegexOptions.IgnoreCase))
            throw new InvalidOperationException("Mach-O links a dynamic graphics library.");
    }

    private static async Task AuditElfVersionsAsync(string executable, string workspace)
    {
        CommandResult readelf = await CommandRunner.RunAsync("readelf", $"--version-info \"{executable}\"", workspace);
        AssertEx.Success(readelf, "ELF symbol version audit");
        await File.WriteAllTextAsync(Path.Combine(workspace, "readelf.txt"), readelf.StandardOutput);
        foreach ((string symbol, Version limit) in new[] { ("GLIBC", new Version(2, 27)), ("GLIBCXX", new Version(3, 4, 24)), ("CXXABI", new Version(1, 3, 11)) })
        {
            foreach (Match match in Regex.Matches(readelf.StandardOutput, $@"\b{symbol}_(\d+(?:\.\d+)+)"))
            {
                if (Version.Parse(match.Groups[1].Value) > limit)
                    throw new InvalidOperationException($"{match.Value} exceeds the {symbol}_{limit} baseline.");
            }
        }
    }
}
