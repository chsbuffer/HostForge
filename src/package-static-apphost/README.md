# ChsBuffer.NETCore.StaticAppHost.win-x64

Static AppHost package for `win-x64`.

The package relinks:
- `AppHost` before `_CreateAppHost` / `_CreateAppHostForPublish`
- `SingleFileHost` before `_CreateSingleFileHost`

It consumes `@(NativeLibrary)` items and supports optional metadata `WholeArchive`.
Additional linker inputs can be passed via `@(LinkerArg)` (for example: system import libs or `/INCLUDE:*` directives).
MSVC toolchain is initialized by `build/findvcvarsall.bat`.
Incremental cache is based on native inputs + host assets + RID/machine/flags fingerprint.

## Usage

```xml
<ItemGroup>
  <PackageReference Include="ChsBuffer.NETCore.StaticAppHost.win-x64" Version="0.2.2" />
</ItemGroup>

<ItemGroup>
  <NativeLibrary Include="$(MSBuildProjectDirectory)\NativeLib\foo.lib" />
  <NativeLibrary Include="$(MSBuildProjectDirectory)\NativeLib\bar.lib">
    <WholeArchive>true</WholeArchive>
  </NativeLibrary>
</ItemGroup>
```

## Package Layout

- `build/StaticAppHost.targets`
- `build/findvcvarsall.bat`
- `build/win-x64/libapphost_obj.lib`
- `build/win-x64/libapphost_lib.lib`
- `build/win-x64/libapphost_directives.lib`
- `build/win-x64/libsinglefilehost_obj.lib`
- `build/win-x64/libsinglefilehost_lib.lib`
- `build/win-x64/libsinglefilehost_directives.lib`
- `build/win-x64/singlefilehost.def`

