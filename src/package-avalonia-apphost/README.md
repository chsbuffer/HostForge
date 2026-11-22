# ChsBuffer.Avalonia.AppHost

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 (10.0.15) and .NET 11 (11.0.0), with the required native libraries statically linked.

## Package selection

| Package | Templates |
|---|---|
| `ChsBuffer.Avalonia.AppHost` | All supported RIDs |
| `ChsBuffer.Avalonia.AppHost.win-x64` | `win-x64` only |
| `ChsBuffer.Avalonia.AppHost.win-arm64` | `win-arm64` only |
| `ChsBuffer.Avalonia.AppHost.linux-x64` | `linux-x64` only |
| `ChsBuffer.Avalonia.AppHost.linux-musl-x64` | `linux-musl-x64` (Alpine Linux) only |
| `ChsBuffer.Avalonia.AppHost.linux-musl-arm64` | `linux-musl-arm64` (Alpine Linux) only |

Use the package without a suffix for all supported targets, or reference only the RID packages the project publishes. RID packages bring in the shared `ChsBuffer.Avalonia.AppHost.Build` integration automatically.

The templates activate for a C# project that targets .NET 10 or .NET 11 (including OS-specific TFMs such as `net11.0-windows`) and builds for the RuntimeIdentifier of an installed RID package: `-r <RID>`, `<RuntimeIdentifier>`, or the RID that `PublishSingleFile` infers. Other builds keep the standard .NET SDK host and publish the SkiaSharp, HarfBuzzSharp and ANGLE dynamic libraries:

- Builds without a RuntimeIdentifier, such as `dotnet run` or a portable `dotnet publish`, keep the libraries of every RID, without a warning.
- Projects with `PublishAot=true` warn that the package is inactive in every build for a RuntimeIdentifier, not only when publishing, because the NativeAOT executable they publish has no apphost; the output of `dotnet build -r <RID>` uses the SDK apphost. The warning is omitted when the project references a HostForge static graphics package, such as `ChsBuffer.SkiaSharp.Static` or `ChsBuffer.Angle.Static`, which link these libraries into the NativeAOT executable.
- `UseAppHost=false` builds run on `dotnet` and warn that the package is inactive.
- F# and Visual Basic projects warn that the package is inactive: the executable resolves the libraries through a C# module initializer that the package compiles into the project.
- Other target framework and RID combinations warn that no template matches.

Each RID package depends on the SkiaSharp and HarfBuzzSharp versions linked into its templates. The managed packages bring only their Windows and macOS native libraries, so the Linux RID packages also depend on `SkiaSharp.NativeAssets.Linux` and `HarfBuzzSharp.NativeAssets.Linux` of these versions; the dynamic libraries then match the managed packages instead of the older ones Avalonia depends on.

The single-file host embeds the native .NET runtime it was built from (10.0.15 or 11.0.0), while the managed framework comes from the runtime pack chosen at publish time. The .NET SDK 11.0.100 selects exactly these runtime packs; with another SDK, set `RuntimeFrameworkVersion` to the template's version for the closest match.

## MSBuild properties

| Property | Default | Effect |
|---|---|---|
| `DisableSkiaHarfBuzzRuntimeCopy` | `true` | Keeps the SkiaSharp and HarfBuzzSharp dynamic libraries and their symbol files out of the publish directory and the single-file bundle. |
| `DisableAngleRuntimeCopy` | `true` | Keeps the ANGLE library (`av_libglesv2.dll`) out of the publish directory and the single-file bundle. |

The package registers a `DllImport` resolver that loads these libraries from the executable with each of the following assemblies that the project references: SkiaSharp, SkiaSharp.Skottie, SkiaSharp.SceneGraph, SkiaSharp.Resources, HarfBuzzSharp and, for Windows RIDs, Avalonia.Win32 (ANGLE). It names them statically, so it loads no assembly the application does not reference and trimming keeps the ones it registers. It registers a library only when the running executable exports it, so the application assembly also works under another host, such as `dotnet App.dll`, a test project that references the application, or the XAML previewer, where the libraries load from their files as usual. A module initializer registers the resolvers before `Main` runs, and `NativeLibrary.SetDllImportResolver` accepts one resolver per assembly: a resolver registered earlier for one of these assemblies stays in effect, and setting the matching property to `false` publishes the libraries it loads; registering one later, for example in `Main`, throws `InvalidOperationException`.

## Licenses

The templates statically link the .NET runtime, Skia, HarfBuzz and the third-party libraries in their archives, ANGLE on Windows, and libc++ on Linux with SkiaSharp 4. Each RID package names `THIRD-PARTY-NOTICES.txt` as its license file; it reproduces the licenses of everything its templates link, including the runtime's own third-party notices. Executables published with the templates contain this code, and these licenses ask binary distributions to reproduce their notices.
