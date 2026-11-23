using System.IO.Compression;
using HostForge.TestInfra;

namespace HostForge.AvaloniaAppHost.Tests;

/// <summary>Checks that a RID package's THIRD-PARTY-NOTICES.txt reproduces the licenses of what its templates link.</summary>
internal static class PackageNotices
{
    /// <summary>The line above and below each section header of THIRD-PARTY-NOTICES.txt.</summary>
    private static readonly string Rule = new('=', 79);

    public static void AssertCoverTemplateInputs(string nupkg, string rid)
    {
        using ZipArchive zip = ZipFile.OpenRead(nupkg);
        PackageLicense.AssertExpression(zip, nupkg, "MIT");
        string notices = PackageLicense.ReadNotices(zip, nupkg);
        HashSet<string> lines = notices.Split('\n').ToHashSet(StringComparer.Ordinal);

        var expected = new List<string> { "skiasharp/skia/LICENSE", "skiasharp/harfbuzz/COPYING" };
        foreach (string tfm in RepoContext.AvaloniaTemplateTfms)
        {
            string runtime = RepoContext.GetHostLibsVersion(tfm);
            expected.Add($"dotnet/{runtime}/LICENSE.TXT");
            expected.Add($"dotnet/{runtime}/THIRD-PARTY-NOTICES.TXT");
        }
        if (rid.StartsWith("win-", StringComparison.Ordinal))
            expected.Add("angle/angle/LICENSE");
        else if (rid.StartsWith("linux-", StringComparison.Ordinal) && RepoContext.SkiaSharpVersion.StartsWith("4.", StringComparison.Ordinal))
            expected.Add("skiasharp/libcxx/LICENSE.TXT");

        string[] missing = expected.Where(section => !lines.Contains(section)).ToArray();
        if (missing.Length != 0)
            throw new InvalidOperationException($"{PackageLicense.Notices} of {nupkg} lacks the sections {string.Join(", ", missing)}.");
        if (!rid.StartsWith("win-", StringComparison.Ordinal) && lines.Any(section => section.StartsWith("angle/", StringComparison.Ordinal)))
            throw new InvalidOperationException($"{PackageLicense.Notices} of {nupkg} names ANGLE, which only the Windows templates link.");

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
                    throw new InvalidOperationException($"{PackageLicense.Notices} of {nupkg} does not reproduce {path} as the section {header}.");
            }
        }
    }
}
