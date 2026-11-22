using System.IO.Compression;
using System.Xml.Linq;

namespace HostForge.TestInfra;

/// <summary>
/// The license metadata of the packed packages: an SPDX expression for the package itself, and the licenses of the
/// third-party code it ships in a separate THIRD-PARTY-NOTICES.txt.
/// </summary>
public static class PackageLicense
{
    public const string Notices = "THIRD-PARTY-NOTICES.txt";

    public static void AssertExpression(ZipArchive zip, string package, string expression)
    {
        XElement license;
        using (Stream stream = zip.Entries.Single(entry => entry.FullName.EndsWith(".nuspec", StringComparison.Ordinal)).Open())
            license = XDocument.Load(stream).Descendants().Single(element => element.Name.LocalName == "license");
        if ((string?)license.Attribute("type") != "expression" || license.Value != expression)
            throw new InvalidOperationException($"The license of {package} is not the expression {expression}: {license}");
    }

    public static string ReadNotices(ZipArchive zip, string package)
    {
        ZipArchiveEntry entry = zip.GetEntry(Notices) ?? throw new InvalidOperationException($"{package} lacks {Notices}.");
        using var reader = new StreamReader(entry.Open());
        return reader.ReadToEnd();
    }
}
