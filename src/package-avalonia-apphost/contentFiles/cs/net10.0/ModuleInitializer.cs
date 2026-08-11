using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;

namespace HostForge.Avalonia.AppHost;

internal static class ModuleInitializer
{
    private static readonly HashSet<string> NativeDllNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "libSkiaSharp",
        "libSkiaSharp.dll",
        "libHarfBuzzSharp",
        "libHarfBuzzSharp.dll",
        "av_libglesv2.dll"
    };

    [ModuleInitializer]
    public static void Init()
    {
        SetResolver(typeof(global::SkiaSharp.SkiaSharpVersion).Assembly);
        SetResolver(typeof(global::HarfBuzzSharp.Blob).Assembly);

#if !HOSTFORGE_DISABLE_AVALONIA_WIN32_IMPORT_RESOLVER
        SetResolver(Assembly.Load("Avalonia.Win32"));
#endif
    }

    private static void SetResolver(Assembly assembly)
    {
        try
        {
            NativeLibrary.SetDllImportResolver(assembly, Resolve);
        }
        catch (InvalidOperationException)
        {
            // Respect a resolver already installed by the application.
        }
    }

    private static IntPtr Resolve(string libraryName, Assembly assembly, DllImportSearchPath? searchPath)
    {
#if DETECT_AVALONIA_DESIGNER
        if (global::Avalonia.Controls.Design.IsDesignMode) return IntPtr.Zero;
#endif
        if (NativeDllNames.Contains(libraryName))
        {
            return NativeLibrary.GetMainProgramHandle();
        }

        return IntPtr.Zero;
    }
}
