using System.IO.Compression;
using System.Xml.Linq;
using HostForge.TestInfra;

namespace HostForge.AvaloniaAppHost.Tests;

/// <summary>Checks that a RID package's license file reproduces the licenses of what its templates link.</summary>
internal static class PackageNotices
{
    /// <summary>The line above and below each section header of THIRD-PARTY-NOTICES.txt.</summary>
    private static readonly string Rule = new('=', 79);

    public static void AssertCoverTemplateInputs(string nupkg, string rid)
    {
        using ZipArchive zip = ZipFile.OpenRead(nupkg);
        ZipArchiveEntry nuspec = zip.Entries.Single(entry => entry.FullName.EndsWith(".nuspec", StringComparison.Ordinal));
        XElement license;
        using (Stream stream = nuspec.Open())
            license = XDocument.Load(stream).Descendants().Single(element => element.Name.LocalName == "license");
        if ((string?)license.Attribute("type") != "file")
            throw new InvalidOperationException($"{nupkg} does not name a license file: {license}");

        ZipArchiveEntry file = zip.GetEntry(license.Value)
            ?? throw new InvalidOperationException($"{nupkg} lacks its license file {license.Value}.");
        string notices;
        using (var reader = new StreamReader(file.Open()))
            notices = reader.ReadToEnd();
        HashSet<string> lines = notices.Split('\n').ToHashSet(StringComparer.Ordinal);

        var expected = new List<string> { "hostforge/LICENSE.TXT", "skiasharp/skia/LICENSE", "skiasharp/harfbuzz/COPYING" };
        foreach (string tfm in RepoContext.AvaloniaTemplateTfms)
        {
            string runtime = RepoContext.GetHostLibsVersion(tfm);
            expected.Add($"dotnet/{runtime}/LICENSE.TXT");
            expected.Add($"dotnet/{runtime}/THIRD-PARTY-NOTICES.TXT");
        }
        if (rid.StartsWith("win-", StringComparison.Ordinal))
            expected.Add("angle/angle/LICENSE");
        else if (RepoContext.SkiaSharpVersion.StartsWith("4.", StringComparison.Ordinal))
            expected.Add("skiasharp/libcxx/LICENSE.TXT");

        string[] missing = expected.Where(section => !lines.Contains(section)).ToArray();
        if (missing.Length != 0)
            throw new InvalidOperationException($"{license.Value} of {nupkg} lacks the sections {string.Join(", ", missing)}.");
        if (!rid.StartsWith("win-", StringComparison.Ordinal) && lines.Any(section => section.StartsWith("angle/", StringComparison.Ordinal)))
            throw new InvalidOperationException($"{license.Value} of {nupkg} names ANGLE, which only the Windows templates link.");

        // The link copies every license of its inputs, each Skia external and ANGLE component included, next to
        // the templates that the package was packed from; each file must be a section headed by its path.
        string artifactRoot = Path.Combine(RepoContext.RepoRoot, "artifacts", "avalonia-host", RepoContext.TargetAvaloniaVersion);
        foreach (string tfm in RepoContext.AvaloniaTemplateTfms)
        {
            string licenses = Path.Combine(artifactRoot, tfm, rid, "licenses");
            foreach (string path in Directory.EnumerateFiles(licenses, "*", SearchOption.AllDirectories))
            {
                string header = Path.GetRelativePath(licenses, path).Replace('\\', '/');
                string text = File.ReadAllText(path).Replace("\r\n", "\n").Trim('\n');
                if (!notices.Contains($"\n{Rule}\n{header}\n{Rule}\n\n{text}\n", StringComparison.Ordinal))
                    throw new InvalidOperationException($"{license.Value} of {nupkg} does not reproduce {path} as the section {header}.");
            }
        }
    }
}
