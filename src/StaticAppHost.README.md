# StaticAppHost

Source-based Static AppHost targets for Windows and Linux. They relink:

- `AppHost` before `_CreateAppHost` / `_CreateAppHostForPublish`
- `SingleFileHost` before `_CreateSingleFileHost`

from the HostLibs static libraries of one RID and the project's `@(NativeLibrary)` items, and hand the relinked host
to the SDK. While `@(NativeLibrary)` is empty, the SDK host is used unchanged.

`samples/simple-pinvoke` imports these targets directly, `package-avalonia-apphost/AvaloniaAppHost.Link.proj` links the
Avalonia host templates with them, and the `ChsBuffer.AppHost.Static` package carries the Windows part
([package README](package-apphost-static/README.md); the package is built from source and not published on nuget.org).

## Inputs

- `@(NativeLibrary)`: static link inputs; `WholeArchive=true` metadata links every member of an archive.
- `@(LinkerArg)`: additional linker arguments, such as system import libraries, `/INCLUDE:` directives or `--sysroot=`.
- `@(LinkerInput)`: files that linker arguments refer to, so that changing them relinks.
- `@(AdditionalNativeLibraryDirectories)`: library search directories.
- `StaticHostAssetsBaseDir`: the HostLibs directory with one folder per RID (`apphost.rsp`, `singlefilehost.rsp` and
  the static libraries they list). It must be set before the import.
- `StaticHostRid`: the RID to link; by default `RuntimeIdentifier`, then `AppHostRuntimeIdentifier`, then
  `DefaultAppHostRuntimeIdentifier`.
- `AppHostLinkPath` / `SingleFileHostLinkPath`: the linked hosts, by default `apphost.static` and
  `singlefilehost.static` (with `.exe` on Windows) in the intermediate output directory.

## Toolchains

Windows (`StaticAppHost.Windows.targets`) links with MSVC `link.exe`, which `findvcvarsall.bat` locates through
vswhere (Desktop development with C++, plus the ARM64 build tools for `win-arm64`). `link.exe` honors only the last
`/DEF`, so the `/DEF:` items of `@(LinkerArg)` are merged into one generated module-definition file per host, for
SingleFileHost together with the runtime's `singlefilehost.def`; such files may contain only an `EXPORTS` section.

Linux (`StaticAppHost.Unix.targets`) links with `CppCompilerAndLinker` (`clang++`, or `g++` when `clang++` is not on
`PATH`) and `LinkerFlavor` (`lld` by default), then strips debug information and unneeded symbols with
`llvm-objcopy` (`objcopy` when `CppCompilerAndLinker` is not Clang or `llvm-objcopy` is missing).
`GroupNativeLibraries=true` wraps the native libraries in `--start-group` / `--end-group` for archives that depend on
each other.

## Incremental builds

Each host has an input stamp next to it (`*.inputs.stamp`) that records the RID, the linker, the link and post-link
commands, and the paths and timestamps of the response file, `@(NativeLibrary)`, `@(LinkerInput)` and module
definitions. A host is relinked only when its stamp changes or the host is missing; a failed link deletes the host.

The hardening and delay-load options (`/guard:*`, `/CETCOMPAT`, `/DELAYLOAD:*`) come from the `<host>.linkflags`
that the host libs record, so the relinked hosts match the official ones: on x64 they link with
`/guard:ehcont`, which fails with `LNK1386` when a `NativeLibrary` has C++ exception handling but was not
compiled with `/guard:ehcont`. Set `StaticHostGuardEHCont=false` to link such libraries without the EH
continuation table.

## Usage

Import the targets after the SDK targets:

```xml
<Project>
  <Import Project="Sdk.props" Sdk="Microsoft.NET.Sdk" />

  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>$(HostLibsTargetFramework)</TargetFramework>
  </PropertyGroup>

  <Import Project="Sdk.targets" Sdk="Microsoft.NET.Sdk" />

  <PropertyGroup>
    <!-- Directory.Build.props points HostLibsRoot at artifacts/hostlibs/<runtime>/<HostLibsFlavor>. -->
    <StaticHostAssetsBaseDir>$(HostLibsRoot)</StaticHostAssetsBaseDir>
  </PropertyGroup>

  <Import Project="$(MSBuildProjectDirectory)\..\..\src\StaticAppHost.targets" />

  <ItemGroup>
    <NativeLibrary Include="$(MSBuildProjectDirectory)\NativeLib\foo.lib" />
    <NativeLibrary Include="$(MSBuildProjectDirectory)\NativeLib\bar.lib">
      <WholeArchive>true</WholeArchive>
    </NativeLibrary>
  </ItemGroup>
</Project>
```

The host libraries embed one .NET runtime, so the project targets the framework of the HostLibs it links
(`HostLibsTargetFramework`). The repository links the `default` HostLibs flavor. On Windows that flavor is compiled with
whole-program optimization (`/GL`), whose objects other MSVC versions may not read; the package therefore ships the
`no-pgo` flavor.

## Source Layout

- `StaticAppHost.targets`: imports the files below, writes the input stamps and defines `LinkAppHost` and
  `LinkSingleFileHost`
- `StaticAppHost.props`: RID, target OS and architecture, HostLibs directory, host and stamp paths
- `StaticAppHost.Windows.targets`: MSVC link arguments and module-definition merging
- `StaticAppHost.Unix.targets`: Clang / GCC link arguments and symbol stripping
- `findvcvarsall.bat`: locates `link.exe` and the MSVC library directories
- `StaticAppHost.README.md`
