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

Each `<tfm>/<rid>/` directory also receives `licenses/`, copied from the inputs of that link:
`dotnet/<runtime-version>/` from the host libs, `skiasharp/` from the SkiaSharp payload (plus
`skiasharp/libcxx/` from `src/package-skiasharp-static/licenses` for Linux SkiaSharp 4), and
`angle/` from the ANGLE payload on Windows. Linking fails when an input has no licenses, which
means it was built by an older recipe.

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

The RID package contains `THIRD-PARTY-NOTICES.txt`, which
[`HostForge.Notices.targets`](../HostForge.Notices.targets) writes at pack time from the `licenses/`
directories of every packed template; sections that are identical for several TFMs appear once. Packing fails when a template directory has no `licenses/`.

The Build package's `.targets` matches `$(RuntimeIdentifier)` and the project's .NET version against
`@(AvaloniaAppHostRids)`. The TFM is normalized to `net<major>.<minor>`, so OS-specific TFMs such as
`net11.0-windows` use the `net11.0` template. Each RID package depends on the Build package, so consumers install only the RID packages they need. The meta-package `ChsBuffer.Avalonia.AppHost` installs every supported RID package.
The packages declare their dependencies in a `net10.0` group only. A project that targets an earlier .NET
therefore gets nothing from the meta-package and only the TFM-independent `.props` from a RID package: the
templates are registered, but without the Build package's `.targets` nothing activates or warns.

`ResolveAvaloniaAppHostActivation` leaves the package inactive, with the native libraries in the output, for:

- builds without a RuntimeIdentifier, silently: their output is portable, and the publish step removes the
  native libraries by file name, which would drop them for every RID;
- builds of a project with `PublishAot=true`, not only its publish, as in `AppHost.Static.targets`, because
  the NativeAOT executable it publishes has no apphost, and `UseAppHost=false` builds, which run on `dotnet`; the NativeAOT
  warning is omitted when any static graphics package is imported (`HostForgeStaticGraphicsImported`);
- projects whose `$(Language)` is not C#, because the resolver is the C# source `ModuleInitializer.cs`
  compiled into the application.

`InjectAvaloniaModuleInitializer` defines `HOSTFORGE_<ASSEMBLY>` for each assembly with `libSkiaSharp`,
`libHarfBuzzSharp` or ANGLE imports that `@(ReferencePath)` contains. `ModuleInitializer.cs` registers its
resolver with exactly those assemblies, and only for the libraries that the main program exports, so the
application assembly keeps the default loading under `dotnet`, test hosts and the previewer.

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
`avalonia-host-skia4-cross-<rid>` artifacts and the Skia 4 `avalonia-host-macos-<version>-<rid>` artifacts,
then uploads the ten Skia 4 packages
as `nuget-avalonia-apphost-skia4`.

## Windows templates

The Windows templates link with the `/guard:*`, `/CETCOMPAT` and `/DELAYLOAD` options that the host libs
record from the runtime's own host links (see `src/StaticAppHost.Windows.targets`). On `win-x64` this
includes `/guard:ehcont`, so every linked object with C++ exception handling must carry EH continuation
metadata, or `link.exe` stops with `LNK2047` and `LNK1386`: the SkiaSharp recipe compiles Skia and
HarfBuzzSharp for `win-x64` with `/guard:ehcont`. ANGLE has no C++ exception handling; for its structured
exception handlers `link.exe` generates the metadata itself and warns with `LNK4291`.

`dumpbin /loadconfig <template>.exe` shows the result: the `win-x64` templates list
`EH Continuation table present` under `Guard Flags` (`IMAGE_GUARD_EH_CONTINUATION_TABLE_PRESENT`, 0x00400000)
and a nonzero `Guard EH continuation count`, as the official hosts do; the official `win-arm64` hosts have no
such table. `task avalonia-test` asserts these guard flags on the linked templates.

## Linux templates

The Linux templates link in the `link` stage of
[`native/hostlibs/linux.Dockerfile`](../../native/hostlibs/linux.Dockerfile), built
from the official build container of the newest runtime in `native/hostlibs/versions.json` for
the RID. The PGO build of the .NET 11 GC is compiled with LTO, so its HostLibs contain LLVM
bitcode that only an LLVM at least as new as the compiling one can link; the container of the
newest runtime has that LLVM. The stage adds fontconfig to the rootfs of the container, from the
package checksums of the SkiaSharp image of the RID, because the templates link `-lfontconfig`. The resulting Linux executables need the
system's `libfontconfig.so.1` at startup.

```sh
docker build $(python scripts/upstream.py build-args) -f native/hostlibs/linux.Dockerfile --target link --build-arg IMAGE=<container> --build-arg RID=linux-x64 -t hostforge-link .
docker run --rm -v "$PWD:/work" -v "$DOTNET_ROOT:$DOTNET_ROOT:ro" -e DOTNET_ROOT hostforge-link \
    bash -c 'PATH="$DOTNET_ROOT:$PATH" task link-avalonia TARGET_OS=linux SYSROOT=/crossrootfs/x64'
docker build $(python scripts/upstream.py build-args) -f native/hostlibs/linux.Dockerfile --target link --build-arg IMAGE=<container> --build-arg RID=linux-musl-arm64 -t hostforge-link .
docker run --rm -v "$PWD:/work" -v "$DOTNET_ROOT:$DOTNET_ROOT:ro" -e DOTNET_ROOT hostforge-link \
    bash -c 'PATH="$DOTNET_ROOT:$PATH" task link-avalonia TARGET_OS=linux-musl RID=linux-musl-arm64 SYSROOT=/crossrootfs/arm64'
```

`<container>` is the entry of the newest runtime for `RID`. For glibc ARM64, build the link
container with `RID=linux-arm64` and run `task link-avalonia TARGET_OS=linux RID=linux-arm64 SYSROOT=/crossrootfs/arm64`
inside it. Each RID uses its own sysroot. Run `task avalonia-test` on a matching glibc system or
in an Alpine container of the same architecture, with `fontconfig`, fonts, `binutils`, and both
.NET runtimes installed.

## macOS templates

Build HostLibs, SkiaSharp and AvaloniaNative for the same RID on a matching macOS runner with Xcode.
`task link-avalonia TARGET_OS=osx RID=osx-arm64` links the templates and ad-hoc signs
them with the .NET host entitlements. Use `osx-x64` on Intel. The Mach-O export lists
expose the graphics imports and retain the .NET single-file diagnostics exports.
The templates link system frameworks using the runner's SDK.

Run `task avalonia-test` on both architectures. It publishes and runs framework-dependent
and self-contained bundles and checks the extraction directory.

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
