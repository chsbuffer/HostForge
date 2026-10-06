using System.Runtime.InteropServices;

Console.WriteLine($"SkiaNative={SkiaSharp.SkiaSharpVersion.Native}");
Console.WriteLine($"HarfBuzzNative={Marshal.PtrToStringUTF8(HarfBuzzNative.GetVersion())}");

if (OperatingSystem.IsWindows())
{
    Console.WriteLine($"AngleError=0x{AngleNative.GetError():X}");
}

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
