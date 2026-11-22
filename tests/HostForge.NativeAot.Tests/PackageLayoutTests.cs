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
            ("SkiaSharp", RepoContext.GetSkiaSharpPackageVersion(skiaVersion), new[] { "win-x64", "win-arm64", "linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64", "osx-x64", "osx-arm64" }),
            ("Angle", RepoContext.AnglePackageVersion, new[] { "win-x64", "win-arm64" }),
            ("Avalonia.Native", RepoContext.AvaloniaNativePackageVersion, new[] { "osx-x64", "osx-arm64" })
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
        string path = Path.Combine(RepoContext.AvaloniaPackageOutputDir, $"ChsBuffer.SkiaSharp.Static.{rid}.{RepoContext.GetSkiaSharpPackageVersion(version)}.nupkg");
        AssertEx.FileExists(path);
        if (new FileInfo(path).Length >= 250_000_000)
            throw new InvalidOperationException("The RID package exceeds the 250 MB publishing limit.");
        using ZipArchive zip = ZipFile.OpenRead(path);
        bool bundlesLibcxx = version.StartsWith("4.", StringComparison.Ordinal) && rid.StartsWith("linux-", StringComparison.Ordinal);
        if (bundlesLibcxx && zip.GetEntry("licenses/libcxx.LICENSE.TXT") is null)
            throw new InvalidOperationException("The bundled libc++ runtime license is missing.");
        string nativeRoot = $"build/native/{rid}/";
        HashSet<string> notices = AssertLicenseFileCoversPayloadLicenses(zip, nativeRoot + "licenses/", "skia/LICENSE", "harfbuzz/COPYING");
        if (bundlesLibcxx && !notices.Contains("libcxx/LICENSE.TXT"))
            throw new InvalidOperationException("THIRD-PARTY-NOTICES.txt lacks the libc++ license.");
        ZipArchiveEntry manifest = zip.GetEntry(nativeRoot + "manifest.json") ?? throw new InvalidOperationException("Missing native manifest.");
        using JsonDocument provenance = await JsonDocument.ParseAsync(manifest.Open());
        foreach (ZipArchiveEntry entry in zip.Entries.Where(entry => entry.FullName.StartsWith(nativeRoot + "licenses/", StringComparison.Ordinal)))
            if (!provenance.RootElement.GetProperty("sha256").TryGetProperty(entry.FullName[nativeRoot.Length..], out _))
                throw new InvalidOperationException($"The native manifest does not cover {entry.FullName}.");
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

    [Test]
    public void AngleRidPackage_LicenseFileCoversTheLicensesOfItsLibraries()
    {
        string rid = NativeAotMatrixTests.Rid;
        if (!rid.StartsWith("win-", StringComparison.Ordinal) || Environment.GetEnvironmentVariable("HOSTFORGE_CHECK_ANGLE") == "false")
            return;

        string path = Path.Combine(RepoContext.AvaloniaPackageOutputDir, $"ChsBuffer.Angle.Static.{rid}.{RepoContext.AnglePackageVersion}.nupkg");
        AssertEx.FileExists(path);
        using ZipArchive zip = ZipFile.OpenRead(path);
        AssertLicenseFileCoversPayloadLicenses(zip, $"build/native/{rid}/licenses/", "angle/LICENSE");
    }

    /// <summary>
    /// Requires the license file of the package to be THIRD-PARTY-NOTICES.txt with a section, headed by its path
    /// below <paramref name="licenseRoot"/>, for every payload license file, and returns the section headers.
    /// </summary>
    private static HashSet<string> AssertLicenseFileCoversPayloadLicenses(ZipArchive zip, string licenseRoot, params string[] required)
    {
        XElement license;
        using (Stream stream = zip.Entries.Single(entry => entry.FullName.EndsWith(".nuspec", StringComparison.Ordinal)).Open())
            license = XDocument.Load(stream).Descendants().Single(element => element.Name.LocalName == "license");
        if ((string?)license.Attribute("type") != "file" || license.Value != "THIRD-PARTY-NOTICES.txt")
            throw new InvalidOperationException($"The package license is not THIRD-PARTY-NOTICES.txt: {license}");
        string notices;
        using (var reader = new StreamReader((zip.GetEntry(license.Value) ?? throw new InvalidOperationException("Missing THIRD-PARTY-NOTICES.txt.")).Open()))
            notices = reader.ReadToEnd();
        HashSet<string> headers = notices.Split('\n').ToHashSet(StringComparer.Ordinal);

        foreach (string name in required)
            if (zip.GetEntry(licenseRoot + name) is null)
                throw new InvalidOperationException($"Missing {licenseRoot}{name}.");
        foreach (ZipArchiveEntry entry in zip.Entries.Where(entry => entry.FullName.StartsWith(licenseRoot, StringComparison.Ordinal)))
        {
            string text;
            using (var reader = new StreamReader(entry.Open()))
                text = reader.ReadToEnd().Replace("\r\n", "\n").Trim('\n');
            if (!headers.Contains(entry.FullName[licenseRoot.Length..]) || !notices.Contains(text, StringComparison.Ordinal))
                throw new InvalidOperationException($"THIRD-PARTY-NOTICES.txt does not reproduce {entry.FullName}.");
        }
        if (!headers.Contains("hostforge/LICENSE.TXT"))
            throw new InvalidOperationException("THIRD-PARTY-NOTICES.txt lacks the HostForge license.");
        return headers;
    }
}
