using System.Runtime.InteropServices;
using SkiaSharp;

Console.WriteLine($"SkiaNative={SkiaSharp.SkiaSharpVersion.Native}");
Console.WriteLine($"HarfBuzzNative={Marshal.PtrToStringUTF8(HarfBuzzNative.GetVersion())}");
Console.WriteLine($"Architecture={RuntimeInformation.ProcessArchitecture}");

using (var bitmap = new SKBitmap(32, 32, SKColorType.Rgba8888, SKAlphaType.Premul))
using (var canvas = new SKCanvas(bitmap))
using (var paint = new SKPaint { Color = SKColors.Red, IsAntialias = false })
{
    canvas.Clear(SKColors.Blue);
    canvas.DrawRect(8, 8, 16, 16, paint);
    for (int y = 0; y < 32; y++)
    for (int x = 0; x < 32; x++)
    {
        SKColor expected = x is >= 8 and < 24 && y is >= 8 and < 24 ? SKColors.Red : SKColors.Blue;
        if (bitmap.GetPixel(x, y) != expected)
            throw new InvalidOperationException($"Pixel mismatch at ({x}, {y}).");
    }
    Console.WriteLine("SoftwarePixels=1024/1024");
}

using (SKTypeface typeface = SKTypeface.FromFamilyName(OperatingSystem.IsWindows() || OperatingSystem.IsMacOS() ? "Arial" : "DejaVu Sans"))
using (SKStreamAsset stream = typeface.OpenStream(out int faceIndex))
using (SKData data = SKData.Create(stream))
using (Stream managedStream = data.AsStream())
using (HarfBuzzSharp.Blob blob = HarfBuzzSharp.Blob.FromStream(managedStream))
using (var face = new HarfBuzzSharp.Face(blob, (uint)faceIndex))
using (var font = new HarfBuzzSharp.Font(face))
using (var buffer = new HarfBuzzSharp.Buffer())
{
    font.SetFunctionsOpenType();
    buffer.AddUtf8("office");
    buffer.GuessSegmentProperties();
    font.Shape(buffer);
    HarfBuzzSharp.GlyphInfo[] glyphs = buffer.GlyphInfos;
    int advance = buffer.GlyphPositions.Sum(position => position.XAdvance);
    if (glyphs.Length is < 1 or > 6 || glyphs.Any(glyph => glyph.Codepoint == 0) || advance <= 0)
        throw new InvalidOperationException("HarfBuzz did not shape the test word into visible advancing glyphs.");
    Console.WriteLine($"HarfBuzzShape=OK Glyphs={glyphs.Length} Advance={advance}");
}

if (OperatingSystem.IsMacOS())
{
    // Skia's CoreText font manager resolves AppKit font weights through dlsym.
    if (!NativeLibrary.TryGetExport(NativeLibrary.GetMainProgramHandle(), "NSFontWeightRegular", out _))
        throw new InvalidOperationException("The macOS font manager's AppKit dependency was not loaded.");
    Console.WriteLine("AppKitFontWeights=OK");
}

#if CHECK_ANGLE
if (OperatingSystem.IsWindows())
{
    Console.WriteLine($"AngleError=0x{AngleNative.GetError():X}");
}
#endif

internal static class AngleNative
{
    [DllImport("av_libglesv2.dll", EntryPoint = "EGL_GetError")]
    internal static extern uint GetError();
}

internal static class HarfBuzzNative
{
    [DllImport("libHarfBuzzSharp", EntryPoint = "hb_version_string")]
    internal static extern nint GetVersion();
}
