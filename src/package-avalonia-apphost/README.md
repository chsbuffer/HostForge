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

The templates activate when the project targets .NET 10 or .NET 11 (including OS-specific TFMs such as `net11.0-windows`) and publishes for an installed RID. Other target combinations keep the standard .NET SDK host behavior.

The single-file host embeds the native .NET runtime it was built from (10.0.15 or 11.0.0), while the managed framework comes from the runtime pack chosen at publish time. The .NET SDK 11.0.100 selects exactly these runtime packs; with another SDK, set `RuntimeFrameworkVersion` to the template's version for the closest match.

## MSBuild properties

| Property | Default | Effect |
|---|---|---|
| `DisableSkiaHarfBuzzRuntimeCopy` | `true` | Keeps the SkiaSharp and HarfBuzzSharp dynamic libraries and their symbol files out of the publish directory and the single-file bundle. |
| `DisableAngleRuntimeCopy` | `true` | Keeps the ANGLE library (`av_libglesv2.dll`) out of the publish directory and the single-file bundle. |

The package registers a `DllImport` resolver for SkiaSharp, HarfBuzzSharp and Avalonia.Win32 that loads these libraries from the executable. When the application has already registered its own resolver for one of these assemblies, that resolver stays in effect; set the matching property to `false` so the libraries it loads are published.
