# Avalonia AppHost Packaging Notes

This file is for repository maintainers. The NuGet README is intentionally limited to package usage.

## Artifact layout

The linker consumes native inputs from:

- `artifacts/hostlibs/<runtime-version>/<flavor>/<rid>/`
- `artifacts/skiasharp/<skiasharp-version>/<rid>/`
- `artifacts/angle/<angle-version>/<rid>/` for Windows

Linked host templates are generated under:

- `artifacts/avalonia-host/<version>/<tfm>/<rid>/`

Examples:

- `artifacts/avalonia-host/12.0/net10.0/win-x64/apphost.exe`
- `artifacts/avalonia-host/12.0/net11.0/linux-x64/singlefilehost`

`AvaloniaTemplateTfms` (default: `HostForgeTargetFrameworks` in `Directory.Build.props`)
selects the template TFMs. Each TFM links the host libs of its own runtime
(`HostLibsNet<N>Version`), from `artifacts/hostlibs/<runtime-version>/...`, so build HostLibs
for every runtime version before linking.
RIDs link one after another; the TFMs of a RID link in parallel because each writes only its own
`<tfm>/<rid>` directory.

## Projects

| Project | Purpose |
|---|---|
| `AvaloniaAppHost.Link.proj` | Links apphost templates for one OS at a time (`TARGET_OS=windows\|linux\|linux-musl`, optionally one `RID`), for every template TFM |
| `AvaloniaAppHost.Rids.targets` | Shared RID definitions for link/pack orchestration |
| `AvaloniaAppHost.Rid.csproj` | Packs one RID's templates for every template TFM and generates its `.props` (`-p:AvaloniaHostRid=win-x64`) |
| `AvaloniaAppHost.Build.csproj` | Packs `.targets` + `ModuleInitializer.cs` |
| `AvaloniaAppHost.csproj` | Meta-package: depends on all RID + Build packages |

## Package layout

Per-RID packages (`ChsBuffer.Avalonia.AppHost.win-x64`, etc.) contain `template/<tfm>/<rid>/` for every
template TFM and register each one in a `.props` file generated at pack time:

```xml
<AvaloniaAppHostRids Include="win-x64">
  <AppHostTemplateTfm>net10.0</AppHostTemplateTfm>
  <AppHostTemplatePath>…/template/net10.0/win-x64/apphost.exe</AppHostTemplatePath>
  <SingleFileHostTemplatePath>…/template/net10.0/win-x64/singlefilehost.exe</SingleFileHostTemplatePath>
</AvaloniaAppHostRids>
<AvaloniaAppHostRids Include="win-x64">
  <AppHostTemplateTfm>net11.0</AppHostTemplateTfm>
  …
</AvaloniaAppHostRids>
```

The Build package's `.targets` matches `$(RuntimeIdentifier)` and the project's .NET version against
`@(AvaloniaAppHostRids)`. The TFM is normalized to `net<major>.<minor>`, so OS-specific TFMs such as
`net11.0-windows` use the `net11.0` template. Each RID package depends on the Build package, so consumers install only the RID packages they need. The meta-package `ChsBuffer.Avalonia.AppHost` installs every supported RID package.

Each RID package also depends on SkiaSharp and HarfBuzzSharp at the linked versions. The managed packages depend on their Windows and macOS native assets but not on the Linux ones, which otherwise stay at the older versions Avalonia.Skia depends on, so the Linux RID packages add `SkiaSharp.NativeAssets.Linux` and `HarfBuzzSharp.NativeAssets.Linux` at the same versions. Builds without an active template, and the Avalonia designer, load these dynamic libraries.

## SkiaSharp variants and package versions

`SKIASHARP_VERSION` selects the native libraries and managed package dependencies
for link, pack and test commands. When omitted, `SkiaSharpVersion` in `Directory.Build.props`
(the Skia 3 version) applies; set it to the Skia 4 version from
`native/skiasharp/versions.json` for Skia 4.
Package IDs are shared, and the Skia 4 variant adds `skia4` to the prerelease part
of every RID, Build and meta-package version (`<major>` is `_Major` in `Directory.Build.props`):

| `_Preview` | SkiaSharp 3 | SkiaSharp 4 |
|---|---|---|
| empty | `12.0.<major>` | `12.0.<major>-skia4` |
| `beta` | `12.0.<major>-beta` | `12.0.<major>-beta.skia4` |

Increment `_Major` whenever the AppHost inputs (.NET runtimes, SkiaSharp, ANGLE) change;
the package lock test fails otherwise. Upstream pull requests and `task upstream-apply`
increment it automatically. See the upstream tracking section of the root
[DEVELOPMENT.md](../../DEVELOPMENT.md#上游跟踪与版本守卫).

Set `_Preview` in `Directory.Build.props`, pass `-p:_Preview=beta` directly to
MSBuild, or set the `_Preview` environment variable before running Task commands.
The `skia4` suffix is added automatically; keep `_Preview` as the desired base label.

Linked templates keep the same `artifacts/avalonia-host/<version>/<tfm>/<rid>/` paths.
For local builds, link and pack one Skia variant before linking the other variant.
CI uses separate jobs and `avalonia-host-skia4-windows` / `avalonia-host-skia4-linux` /
`avalonia-host-skia4-linux-musl-<arch>` artifacts, then uploads the seven Skia 4 packages
as `nuget-avalonia-apphost-skia4`.

## Linux templates

The Linux templates link in the `link` stage of
[`native/hostlibs/linux.Dockerfile`](../../native/hostlibs/linux.Dockerfile), built
from the official build container of the newest runtime in `native/hostlibs/versions.json` for
the RID. The PGO build of the .NET 11 GC is compiled with LTO, so its HostLibs contain LLVM
bitcode that only an LLVM at least as new as the compiling one can link; the container of the
newest runtime has that LLVM. The stage adds fontconfig to the rootfs of the container, from the
package checksums of the SkiaSharp image of the RID, because the templates link `-lfontconfig`.

```sh
docker build $(python scripts/upstream.py build-args) -f native/hostlibs/linux.Dockerfile --target link --build-arg IMAGE=<container> --build-arg RID=linux-x64 -t hostforge-link .
docker run --rm -v "$PWD:/work" -v "$DOTNET_ROOT:$DOTNET_ROOT:ro" -e DOTNET_ROOT hostforge-link \
    bash -c 'PATH="$DOTNET_ROOT:$PATH" task link-avalonia TARGET_OS=linux SYSROOT=/crossrootfs/x64'
docker build $(python scripts/upstream.py build-args) -f native/hostlibs/linux.Dockerfile --target link --build-arg IMAGE=<container> --build-arg RID=linux-musl-arm64 -t hostforge-link .
docker run --rm -v "$PWD:/work" -v "$DOTNET_ROOT:$DOTNET_ROOT:ro" -e DOTNET_ROOT hostforge-link \
    bash -c 'PATH="$DOTNET_ROOT:$PATH" task link-avalonia TARGET_OS=linux-musl RID=linux-musl-arm64 SYSROOT=/crossrootfs/arm64'
```

`<container>` is the entry of the newest runtime for `RID`. Each musl RID needs its own sysroot,
so `TARGET_OS=linux-musl` links one `RID` at a time. Run `task avalonia-test` for `linux-x64` on a
glibc system with `binutils` and the .NET 10 and 11 runtimes, and for a musl RID in an Alpine
container of the same architecture with `fontconfig`, `binutils` and the .NET 10 runtime installed;
CI uses the Alpine .NET SDK image of `test-skia-linux-cross`.

## Local commands

Link one OS:

```powershell
task link-avalonia TARGET_OS=windows
```

Link Linux in the containers of [Linux templates](#linux-templates).

Select Skia 4 with the same option for every step (`<skia4>` is the Skia 4 version in
`native/skiasharp/versions.json`):

```powershell
task link-avalonia TARGET_OS=windows SKIASHARP_VERSION=<skia4>
task avalonia-test SKIASHARP_VERSION=<skia4>
task pack-avalonia SKIASHARP_VERSION=<skia4>
```

Before packing all RIDs, link the Linux RIDs with `SKIASHARP_VERSION=<skia4>` in the containers of
[Linux templates](#linux-templates).

Pack one RID (requires linked templates for that RID):

```powershell
task pack-avalonia-rid RID=win-x64
task pack-avalonia-rid RID=linux-x64
task pack-avalonia-rid RID=linux-musl-arm64
```

Pack Build package:

```powershell
task pack-avalonia-build
```

Pack everything (RIDs + Build + meta):

```powershell
task pack-avalonia
```

Pack only selected RIDs directly:

```powershell
dotnet pack src/package-avalonia-apphost/AvaloniaAppHost.Rid.csproj -c Release -p:AvaloniaHostRid=win-x64
```
