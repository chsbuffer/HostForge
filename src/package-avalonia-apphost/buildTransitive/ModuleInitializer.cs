using System;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;

namespace HostForge.Avalonia.AppHost;

// The build defines HOSTFORGE_<ASSEMBLY> for each assembly registered below that the project references.
// The static type references load no assembly the application lacks, and trimming keeps the assemblies they name.
internal static class ModuleInitializer
{
    private static readonly IntPtr MainProgram = NativeLibrary.GetMainProgramHandle();

    [ModuleInitializer]
    public static void Init()
    {
        // The application assembly also runs under other hosts, such as dotnet, a test host or the previewer.
        // Their main program exports none of the libraries, which then load from their files as usual.
        if (Exports("sk_version_get_milestone"))
            RegisterSkiaSharp();
        if (Exports("hb_version_string"))
            RegisterHarfBuzzSharp();
        if (Exports("EGL_GetError"))
            RegisterAngle();
    }

    // The registrations are separate methods so that the JIT loads their assemblies only when the
    // executable exports the library.
    [MethodImpl(MethodImplOptions.NoInlining)]
    private static void RegisterSkiaSharp()
    {
#if HOSTFORGE_SKIASHARP
        SetResolver(typeof(global::SkiaSharp.SkiaSharpVersion).Assembly, "libSkiaSharp");
#endif
        // Skottie, SceneGraph and Resources import libSkiaSharp themselves; the templates export their functions too.
#if HOSTFORGE_SKIASHARP_SKOTTIE
        SetResolver(typeof(global::SkiaSharp.Skottie.Animation).Assembly, "libSkiaSharp");
#endif
#if HOSTFORGE_SKIASHARP_SCENEGRAPH
        SetResolver(typeof(global::SkiaSharp.SceneGraph.InvalidationController).Assembly, "libSkiaSharp");
#endif
#if HOSTFORGE_SKIASHARP_RESOURCES
        SetResolver(typeof(global::SkiaSharp.Resources.ResourceProvider).Assembly, "libSkiaSharp");
#endif
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static void RegisterHarfBuzzSharp()
    {
#if HOSTFORGE_HARFBUZZSHARP
        SetResolver(typeof(global::HarfBuzzSharp.Blob).Assembly, "libHarfBuzzSharp");
#endif
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static void RegisterAngle()
    {
#if HOSTFORGE_AVALONIA_WIN32
        SetResolver(typeof(global::Avalonia.Win32PlatformOptions).Assembly, "av_libglesv2");
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
