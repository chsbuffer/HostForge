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

Directly import:
```xml
<Project>
  <Import Project="Sdk.props" Sdk="Microsoft.NET.Sdk" />

  <PropertyGroup>
    <HostLibsFlavor Condition="'$(OS)' == 'Windows_NT'">no-pgo</HostLibsFlavor>
    <HostLibsRoot>$([MSBuild]::NormalizePath('$(HostLibsBaseRoot)', '$(HostLibsFlavor)'))</HostLibsRoot>
    <StaticHostAssetsBaseDir>$(HostLibsRoot)</StaticHostAssetsBaseDir>
  </PropertyGroup>

  <Import Project="Sdk.targets" Sdk="Microsoft.NET.Sdk" />
  <Import Project="$(MSBuildProjectDirectory)\..\..\src\StaticAppHost.targets" />

  <ItemGroup>
    <NativeLibrary Include="$(MSBuildProjectDirectory)\NativeLib\foo.lib" />
    <NativeLibrary Include="$(MSBuildProjectDirectory)\NativeLib\bar.lib">
      <WholeArchive>true</WholeArchive>
    </NativeLibrary>
  </ItemGroup>
```

NuGet: TBD

## Source Layout

- `StaticAppHost.targets`
- `StaticAppHost.Windows.targets`
- `findvcvarsall.bat`
- `StaticAppHost.README.md`
