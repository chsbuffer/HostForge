# ChsBuffer.AppHost.Static.win-x64

Packages the .NET host relinking targets together with the .NET 10 `win-x64` static libraries for `apphost` and `singlefilehost`.

Use this package when you want the generated executable to contain native code directly, instead of deploying separate native DLLs alongside it.

On supported builds, the imported targets relink `apphost` and `singlefilehost` from the project's `NativeLibrary` items, replacing the default SDK host with one that already contains the requested native libraries.

## Requirements

- MSVC toolchain
- One or more `NativeLibrary` items provided by the project or by another package, e.g. ChsBuffer.SkiaSharp.Static.win-x64

## Usage

This package activates automatically when the effective apphost RID resolves to `win-x64`.

The relink step consumes:

- `@(NativeLibrary)` as static link inputs
- Optional `WholeArchive=true` metadata on selected `NativeLibrary` items
- Optional `@(LinkerArg)` items for additional linker arguments

Unlike NativeAOT, this relinking flow does not consume `DirectPInvoke` items to emit direct native references for the final executable. You must make sure any required exports are preserved yourself.

Common approaches are:

- Set `WholeArchive=true` on selected `NativeLibrary` items
- Pass explicit export control through `@(LinkerArg)`, for example by supplying a `.def` file

After that, you can P/Invoke directly against the main executable. If you are using a prebuilt binding assembly, use `NativeLibrary.SetDllImportResolver` to redirect its `DllImport` calls to `NativeLibrary.GetMainProgramHandle()`.

Example for SkiaSharp:

```csharp
using System;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;

internal static class ModuleInitializer
{
    [ModuleInitializer]
    internal static void Init()
    {
        NativeLibrary.SetDllImportResolver(
            typeof(SkiaSharp.SkiaSharpVersion).Assembly,
            static (libraryName, _, _) =>
                libraryName is "libSkiaSharp.dll"
                    ? NativeLibrary.GetMainProgramHandle()
                    : IntPtr.Zero);
    }
}
```
