using System;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;

namespace HostForge.Avalonia.AppHost;

internal static class ModuleInitializer
{
    private static readonly IntPtr MainProgram = NativeLibrary.GetMainProgramHandle();

    [ModuleInitializer]
    public static void Init()
    {
        // The application assembly also runs under other hosts, such as dotnet, a test host or the previewer.
        // Their main program exports none of the libraries, which then load from their files as usual.
        if (Exports("sk_version_get_milestone"))
            SetResolver(typeof(global::SkiaSharp.SkiaSharpVersion).Assembly, "libSkiaSharp");
        if (Exports("hb_version_string"))
            SetResolver(typeof(global::HarfBuzzSharp.Blob).Assembly, "libHarfBuzzSharp");

#if !HOSTFORGE_DISABLE_AVALONIA_WIN32_IMPORT_RESOLVER
        if (Exports("EGL_GetError"))
            SetResolver(Assembly.Load("Avalonia.Win32"), "av_libglesv2");
#endif
    }

    private static bool Exports(string symbol) => NativeLibrary.TryGetExport(MainProgram, symbol, out _);

    private static void SetResolver(Assembly assembly, string library)
    {
        try
        {
            NativeLibrary.SetDllImportResolver(assembly, (libraryName, _, _) =>
                string.Equals(Path.GetFileNameWithoutExtension(libraryName), library, StringComparison.OrdinalIgnoreCase)
                    ? MainProgram
                    : IntPtr.Zero);
        }
        catch (InvalidOperationException)
        {
            // Respect a resolver already installed by the application.
        }
    }
}
