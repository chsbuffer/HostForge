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
        "libHarfBuzzSharp.dll"
    };

    [ModuleInitializer]
    public static void Init()
    {
        NativeLibrary.SetDllImportResolver(typeof(global::SkiaSharp.SkiaSharpVersion).Assembly, Resolve);
        NativeLibrary.SetDllImportResolver(typeof(global::HarfBuzzSharp.Blob).Assembly, Resolve);
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
