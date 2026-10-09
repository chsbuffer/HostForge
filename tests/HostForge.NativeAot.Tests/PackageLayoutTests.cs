using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using System.Xml.Linq;
using HostForge.TestInfra;

namespace HostForge.NativeAot.Tests;

public class PackageLayoutTests
{
    [Test]
    public async Task MetaPackages_RequireEverySupportedRidAtTheExactVersion()
    {
        if (Environment.GetEnvironmentVariable("HOSTFORGE_TEST_META") != "true")
            return;

        string skiaVersion = Environment.GetEnvironmentVariable("HOSTFORGE_SKIA_VERSION") ?? RepoContext.SkiaSharpVersion;
        foreach ((string component, string version, string[] rids) in new[]
        {
            ("SkiaSharp", skiaVersion, new[] { "win-x64", "win-arm64", "linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64", "osx-x64", "osx-arm64" }),
            ("Angle", RepoContext.AngleVersion, new[] { "win-x64", "win-arm64" }),
            ("Avalonia.Native", "12.1.3", new[] { "osx-x64", "osx-arm64" })
        })
        {
            string id = $"ChsBuffer.{component}.Static";
            string package = Path.Combine(RepoContext.AvaloniaPackageOutputDir, $"{id}.{version}.nupkg");
            using ZipArchive zip = ZipFile.OpenRead(package);
            using Stream stream = zip.Entries.Single(entry => entry.FullName.EndsWith(".nuspec", StringComparison.Ordinal)).Open();
            XElement[] dependencies = XDocument.Load(stream).Descendants().Where(element => element.Name.LocalName == "dependency").ToArray();
            await Assert.That(dependencies.Length).IsEqualTo(rids.Length);
            foreach (string rid in rids)
            {
                XElement dependency = dependencies.Single(element => (string?)element.Attribute("id") == $"{id}.{rid}");
                await Assert.That((string?)dependency.Attribute("version")).IsEqualTo($"[{version}]");
            }
        }
    }

    [Test]
    public async Task RidPackage_HasPortableLayoutAndVerifiablePayload()
    {
        string version = Environment.GetEnvironmentVariable("HOSTFORGE_SKIA_VERSION") ?? RepoContext.SkiaSharpVersion;
        string rid = NativeAotMatrixTests.Rid;
        string path = Path.Combine(RepoContext.AvaloniaPackageOutputDir, $"ChsBuffer.SkiaSharp.Static.{rid}.{version}.nupkg");
        AssertEx.FileExists(path);
        if (new FileInfo(path).Length >= 250_000_000)
            throw new InvalidOperationException("The RID package exceeds the 250 MB publishing limit.");
        using ZipArchive zip = ZipFile.OpenRead(path);
        if (version == "4.153.1" && rid.StartsWith("linux-", StringComparison.Ordinal) && zip.GetEntry("licenses/libcxx.LICENSE.TXT") is null)
            throw new InvalidOperationException("The bundled libc++ runtime license is missing.");
        string nativeRoot = $"build/native/{rid}/";
        ZipArchiveEntry manifest = zip.GetEntry(nativeRoot + "manifest.json") ?? throw new InvalidOperationException("Missing native manifest.");
        using JsonDocument provenance = await JsonDocument.ParseAsync(manifest.Open());
        if (rid.StartsWith("linux-", StringComparison.Ordinal) && provenance.RootElement.TryGetProperty("libc", out JsonElement libc) &&
            libc.GetString() != (rid.StartsWith("linux-musl-", StringComparison.Ordinal) ? "musl" : "glibc"))
            throw new InvalidOperationException("The package contains a different libc target.");
        foreach (JsonProperty file in provenance.RootElement.GetProperty("sha256").EnumerateObject())
        {
            ZipArchiveEntry entry = zip.GetEntry(nativeRoot + file.Name) ?? throw new InvalidOperationException($"Missing manifest file {file.Name}.");
            using Stream stream = entry.Open();
            string hash = Convert.ToHexString(await SHA256.HashDataAsync(stream));
            if (!hash.Equals(file.Value.GetString(), StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException($"SHA-256 mismatch for {file.Name}.");
        }
        foreach (ZipArchiveEntry entry in zip.Entries.Where(entry => entry.FullName.EndsWith(".a", StringComparison.Ordinal)))
        {
            using Stream stream = entry.Open();
            byte[] header = new byte[8];
            stream.ReadExactly(header);
            if (!header.AsSpan().SequenceEqual("!<arch>\n"u8))
                throw new InvalidOperationException($"Archive {entry.FullName} is not self-contained (thin archives cannot be shipped).");
        }
        if (zip.Entries.Any(entry => entry.FullName.Contains("/default/", StringComparison.Ordinal) || entry.FullName.EndsWith(".dll", StringComparison.OrdinalIgnoreCase)))
            throw new InvalidOperationException("The static package includes a variant directory or dynamic payload.");
        Console.WriteLine($"Verified {path}: {new FileInfo(path).Length} bytes and all manifest hashes.");
    }
}
