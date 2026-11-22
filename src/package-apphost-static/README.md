# ChsBuffer.AppHost.Static.win-x64

Packages the .NET host relinking targets together with the `win-x64` static libraries for `apphost` and `singlefilehost`.
The bundled Windows host inputs are built from the `no-pgo` HostForge flavor so they do not depend on whole-program optimization.

The package is not published on nuget.org yet. Build it from the HostForge repository on Windows:

```powershell
task build-hostlibs PGO=False HOSTLIBS_VERSION=<runtime>
task pack-static-apphost TFM=<target framework>
```

Without `HOSTLIBS_VERSION` and `TFM`, both use the repository's default `HostLibsTargetFramework`. The package is
written to `artifacts/packages/Release`; add that directory as a package source to restore it.

Each package version contains the host libraries of one .NET runtime and activates only for that target framework:

| Package version | Target framework | .NET runtime |
|---|---|---|
| `10.0.15-alpha.1` | `net10.0` | 10.0.15 |
| `11.0.0-alpha.1` | `net11.0` | 11.0.0 |

Use this package when you want the generated executable to contain native code directly, instead of deploying separate native DLLs alongside it.

The host libraries are the .NET runtime's own native code. Its `LICENSE.TXT` and `THIRD-PARTY-NOTICES.TXT`, which cover the third-party code in that runtime, are under `build/win-x64/licenses/`.

On supported builds, the imported targets relink `apphost` and `singlefilehost` from the project's `NativeLibrary` items, replacing the default SDK host with one that already contains the requested native libraries.

## Requirements

- MSVC toolchain
- One or more `NativeLibrary` items, which the project declares itself. Without them the SDK host is used unchanged, without a warning.

The NativeAOT packages of HostForge, such as `ChsBuffer.SkiaSharp.Static`, add their `NativeLibrary` items only when publishing with NativeAOT, which this package leaves to the SDK, so they provide no input here.

## Usage

This package activates automatically when the project targets the package's .NET version and the effective apphost RID resolves to `win-x64`. NativeAOT publishing is left unchanged.

The relink step consumes:

- `@(NativeLibrary)` as static link inputs
- Optional `WholeArchive=true` metadata on selected `NativeLibrary` items
- Optional `@(LinkerArg)` items for additional linker arguments

Unlike NativeAOT, this relinking flow does not consume `DirectPInvoke` items to emit direct native references for the final executable. You must make sure any required exports are preserved yourself.

Common approaches are:

- Set `WholeArchive=true` on selected `NativeLibrary` items
- Pass explicit export control through `@(LinkerArg)`, for example by supplying a `.def` file

`link.exe` honors only one `/DEF`, and `singlefilehost` needs the runtime's own exports. `/DEF:` items in
`@(LinkerArg)` are therefore merged with those exports into one generated module-definition file per host;
such files may contain only an `EXPORTS` section.

After that, you can P/Invoke directly against the main executable. If you are using a prebuilt binding assembly, use `NativeLibrary.SetDllImportResolver` to redirect its `DllImport` calls to `NativeLibrary.GetMainProgramHandle()`.
The resolver receives the library name as the `DllImport` attribute spells it: SkiaSharp and HarfBuzzSharp use `libSkiaSharp` and `libHarfBuzzSharp`, without an extension.

Example for SkiaSharp and HarfBuzzSharp:

```csharp
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;

internal static class ModuleInitializer
{
    private static readonly HashSet<string> StaticLibraryNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "libSkiaSharp", "libSkiaSharp.dll", "libHarfBuzzSharp", "libHarfBuzzSharp.dll"
    };

    [ModuleInitializer]
    internal static void Init()
    {
        NativeLibrary.SetDllImportResolver(typeof(SkiaSharp.SkiaSharpVersion).Assembly, Resolve);
        NativeLibrary.SetDllImportResolver(typeof(HarfBuzzSharp.Blob).Assembly, Resolve);
    }

    private static IntPtr Resolve(string libraryName, Assembly assembly, DllImportSearchPath? searchPath) =>
        StaticLibraryNames.Contains(libraryName) ? NativeLibrary.GetMainProgramHandle() : IntPtr.Zero;
}
```

## Control Flow Guard and EH continuation metadata

The relinked hosts keep the hardening of the official ones, from the link flags recorded with the bundled
host libraries: Control Flow Guard (`/guard:cf`), CET compatibility (`/CETCOMPAT`) and an EH continuation
table (`/guard:ehcont`), against which Windows checks where exception handling resumes when CET shadow stacks
are enforced.

`link.exe` builds that table only when every object with C++ exception handling was compiled with
`/guard:ehcont` (MSVC and clang-cl; `<GuardEHContMetadata>true</GuardEHContMetadata>` in a `.vcxproj`).
A `NativeLibrary` with C++ exception handling compiled without it fails the relink with `LNK2047` and
`LNK1386`. Rebuild such libraries with `/guard:ehcont`; if you cannot, link the hosts without the table:

```xml
<PropertyGroup>
  <StaticHostGuardEHCont>false</StaticHostGuardEHCont>
</PropertyGroup>
```

Do not pass `/force:guardehcont` through `@(LinkerArg)` instead: the table would lack the continuation
targets of those libraries, and a process with enforced CET shadow stacks terminates when an exception is
caught in them.
