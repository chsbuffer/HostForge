# ChsBuffer.Avalonia.AppHost

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 (10.0.15) and .NET 11 (11.0.0), with SkiaSharp, HarfBuzzSharp and, on Windows, ANGLE statically linked. Published applications need no separate copies of these native libraries.

## Packages

| Package | Templates |
|---|---|
| `ChsBuffer.Avalonia.AppHost` | All supported RIDs |
| `ChsBuffer.Avalonia.AppHost.win-x64` | `win-x64` only |
| `ChsBuffer.Avalonia.AppHost.win-arm64` | `win-arm64` only |
| `ChsBuffer.Avalonia.AppHost.linux-x64` | `linux-x64` only |
| `ChsBuffer.Avalonia.AppHost.linux-musl-x64` | `linux-musl-x64` (Alpine Linux) only |
| `ChsBuffer.Avalonia.AppHost.linux-musl-arm64` | `linux-musl-arm64` (Alpine Linux) only |

Each RID package depends on the SkiaSharp and HarfBuzzSharp versions linked into its templates.

## Activation

The templates replace the SDK host when a C# project that targets .NET 10 or .NET 11 (including OS-specific TFMs such as `net11.0-windows`) builds for the RuntimeIdentifier of a referenced RID package: `-r <RID>`, `<RuntimeIdentifier>`, or the RID that `PublishSingleFile` infers.

Other builds keep the SDK host and publish the dynamic libraries. Builds without a RuntimeIdentifier and projects that target an earlier .NET do so silently. `UseAppHost=false`, F# and Visual Basic projects, and RIDs without a template warn. `PublishAot=true` warns unless the project references a HostForge static graphics package such as `ChsBuffer.SkiaSharp.Static`, which links the libraries into the NativeAOT executable.

The single-file host embeds the native .NET runtime it was built from (10.0.15 or 11.0.0), while the managed framework comes from the runtime pack chosen at publish time. The .NET SDK 11.0.100 selects exactly these runtime packs; with another SDK, set `RuntimeFrameworkVersion` to the template's version.

## MSBuild properties

| Property | Default | Effect |
|---|---|---|
| `DisableSkiaHarfBuzzRuntimeCopy` | `true` | Keeps the SkiaSharp and HarfBuzzSharp dynamic libraries out of the publish directory and the single-file bundle. |
| `DisableAngleRuntimeCopy` | `true` | Keeps the ANGLE library (`av_libglesv2.dll`) out of the publish directory and the single-file bundle. |

A module initializer registers the `DllImport` resolvers that load these libraries from the executable. Registering another resolver for SkiaSharp, HarfBuzzSharp or Avalonia.Win32 later, for example in `Main`, throws `InvalidOperationException`; one registered earlier stays in effect, and the matching property must then be `false` so that the libraries are published.
