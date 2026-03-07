# StaticAppHost

Source-based Static AppHost targets for Windows.

The targets relink:
- `AppHost` before `_CreateAppHost` / `_CreateAppHostForPublish`
- `SingleFileHost` before `_CreateSingleFileHost`

They consume `@(NativeLibrary)` items and support optional metadata `WholeArchive`.
Additional linker inputs can be passed via `@(LinkerArg)` (for example: system import libs or `/INCLUDE:*` directives).
MSVC toolchain is initialized by `findvcvarsall.bat`.
Incremental cache is based on native inputs + host assets + RID/machine/flags fingerprint.

## Usage

```xml
<PropertyGroup>
  <StaticHostRid Condition="'$(StaticHostRid)' == ''">$(DefaultRid)</StaticHostRid>
</PropertyGroup>

<Import Project="$(MSBuildProjectDirectory)\..\..\src\StaticAppHost.targets" />

<PropertyGroup>
  <StaticHostAssetsDir>$(HostLibsRoot)\$(StaticHostRid)</StaticHostAssetsDir>
</PropertyGroup>

<ItemGroup>
  <NativeLibrary Include="$(MSBuildProjectDirectory)\NativeLib\foo.lib" />
  <NativeLibrary Include="$(MSBuildProjectDirectory)\NativeLib\bar.lib">
    <WholeArchive>true</WholeArchive>
  </NativeLibrary>
</ItemGroup>
```

## Source Layout

- `StaticAppHost.targets`
- `StaticAppHost.Windows.targets`
- `findvcvarsall.bat`
- `StaticAppHost.README.md`
