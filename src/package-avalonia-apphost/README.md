# ChsBuffer.Avalonia.AppHost

Prelinked Avalonia apphost/singlefilehost template package for `win-x64` and `win-arm64`.

## Important first

- Current implementation is intended for **Avalonia 11**.
- Template payload always includes `win-x64`; `win-arm64` is included when local arm64 host/native inputs are present at pack time.
- `AngleEgl` is not statically linked by this package.
- Avalonia 11 Win32 default rendering mode is typically `AngleEgl, Software`.
- If `av_libglesv2.dll` is not present, default behavior may leave only software rendering available.

To avoid software-only fallback, explicitly include additional rendering backends:

```csharp
private static AppBuilder BuildAvaloniaApp()
{
  return AppBuilder.Configure<App>()
      .UsePlatformDetect()
      .LogToTrace()
      .With(new Win32PlatformOptions
      {
          RenderingMode =
          [
              Win32RenderingMode.AngleEgl,
              Win32RenderingMode.Vulkan,
              Win32RenderingMode.Wgl,
              Win32RenderingMode.Software
          ]
      });
}
```

With this configuration, `av_libglesv2.dll` can be removed when a non-ANGLE backend is available on target machines.

## Install

```powershell
dotnet add package ChsBuffer.Avalonia.AppHost
```

## What this package provides

- `template/net10.0/win-x64/apphost.exe`
- `template/net10.0/win-x64/singlefilehost.exe`
- `template/net10.0/win-arm64/apphost.exe` (when arm64 pack inputs exist)
- `template/net10.0/win-arm64/singlefilehost.exe` (when arm64 pack inputs exist)
- `buildTransitive/AvaloniaAppHost.props`
  - sets `AppHostSourcePath` and `SingleFileHostSourcePath` from the package template path
- `buildTransitive/AvaloniaAppHost.targets`
  - optional suppression of SkiaSharp/HarfBuzz native runtime copy
- `contentFiles/cs/net10.0/ModuleInitializer.cs`
  - maps Avalonia native DLL names to the main program handle

### Arm64 template inclusion inputs

To include `win-arm64` templates when packing this package, both directories must exist:

- `artifacts/hostlibs/win-arm64`
- `artifacts/skiasharp-2.88.9/win-arm64`

## NativeAssets behavior

By default this package sets:

```xml
<DisableSkiaHarfBuzzRuntimeCopy>true</DisableSkiaHarfBuzzRuntimeCopy>
```

That removes these files from build/publish output:

- `libSkiaSharp.dll`
- `libHarfBuzzSharp.dll`

### Switch back to default SDK copy behavior

Set this in your app project:

```xml
<PropertyGroup>
  <DisableSkiaHarfBuzzRuntimeCopy>false</DisableSkiaHarfBuzzRuntimeCopy>
</PropertyGroup>
```

### Alternative: explicitly exclude runtime/native assets yourself

If you prefer explicit package-level control, disable the package switch above and add:

```xml
<ItemGroup>
  <PackageReference Include="SkiaSharp.NativeAssets.Win32" Version="2.88.9" ExcludeAssets="runtime;native" />
  <PackageReference Include="HarfBuzzSharp.NativeAssets.Win32" Version="8.3.1.1" ExcludeAssets="runtime;native" />
</ItemGroup>
```

`IncludeAssets="none"` can also be used if you want to exclude all assets from those packages.
