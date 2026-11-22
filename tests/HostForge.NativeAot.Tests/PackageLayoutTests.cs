using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using System.Xml.Linq;
using HostForge.TestInfra;

namespace HostForge.NativeAot.Tests;

public class PackageLayoutTests
{
    [Test]
    public async Task RidPackage_HasPortableLayoutAndVerifiablePayload()
    {
        string version = NativeAotMatrixTests.SkiaVersion;
        string rid = NativeAotMatrixTests.Rid;
        string path = Path.Combine(RepoContext.AvaloniaPackageOutputDir, $"ChsBuffer.SkiaSharp.Static.{rid}.{RepoContext.GetSkiaSharpPackageVersion(version)}.nupkg");
        AssertEx.FileExists(path);
        if (new FileInfo(path).Length >= 250_000_000)
            throw new InvalidOperationException("The RID package exceeds the 250 MB publishing limit.");
        using ZipArchive zip = ZipFile.OpenRead(path);
        bool bundlesLibcxx = version.StartsWith("4.", StringComparison.Ordinal) && rid.StartsWith("linux-", StringComparison.Ordinal);
        if (bundlesLibcxx && zip.GetEntry("licenses/libcxx.LICENSE.TXT") is null)
            throw new InvalidOperationException("The bundled libc++ runtime license is missing.");
        await AssertGraphicsDependenciesAsync(zip, version, rid);
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

    // SkiaSharp and HarfBuzzSharp depend on their Windows and macOS native assets but not on the Linux ones, so the
    // Linux RID packages add those at the managed versions. Upstream excludes the same assets from these dependencies.
    private static async Task AssertGraphicsDependenciesAsync(ZipArchive zip, string skiaVersion, string rid)
    {
        string harfBuzzVersion = RepoContext.GetHarfBuzzVersion(skiaVersion);
        var expected = new List<string> { $"SkiaSharp {skiaVersion}", $"HarfBuzzSharp {harfBuzzVersion}" };
        if (rid.StartsWith("linux-", StringComparison.Ordinal))
            expected.AddRange([$"SkiaSharp.NativeAssets.Linux {skiaVersion}", $"HarfBuzzSharp.NativeAssets.Linux {harfBuzzVersion}"]);

        using Stream stream = zip.Entries.Single(entry => entry.FullName.EndsWith(".nuspec", StringComparison.Ordinal)).Open();
        XElement[] dependencies = XDocument.Load(stream).Descendants().Where(element => element.Name.LocalName == "dependency").ToArray();
        IEnumerable<string> actual = dependencies.Select(element => $"{(string?)element.Attribute("id")} {(string?)element.Attribute("version")}");
        await Assert.That(string.Join(", ", actual.Order(StringComparer.Ordinal)))
            .IsEqualTo(string.Join(", ", expected.Order(StringComparer.Ordinal)));
        foreach (XElement dependency in dependencies)
            await Assert.That((string?)dependency.Attribute("exclude")).IsEqualTo("Build,Analyzers");
    }

    [Test]
    public void AngleRidPackage_LicenseFileCoversTheLicensesOfItsLibraries()
    {
        string rid = NativeAotMatrixTests.Rid;
        if (!rid.StartsWith("win-", StringComparison.Ordinal) || Environment.GetEnvironmentVariable("HOSTFORGE_CHECK_ANGLE") == "false")
            Skip.Test("The ANGLE packages exist for Windows RIDs, and HOSTFORGE_CHECK_ANGLE=false skips them.");

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
