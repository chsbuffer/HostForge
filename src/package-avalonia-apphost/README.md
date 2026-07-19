# ChsBuffer.Avalonia.AppHost

Apphost and single-file host templates for Avalonia, prelinked against SkiaSharp and HarfBuzz native libraries, for **.NET 10.0** on Windows and Linux.

This package replaces the SDK apphost templates for supported Avalonia publish targets and removes SkiaSharp/HarfBuzz native runtime files from publish output by default.

Supported template RIDs:

- `win-x64`
- `win-arm64`
- `linux-x64`

## Package version compatibility

- `12.0.0`: supports Avalonia 12 and is prelinked against `SkiaSharp 3.119.4` + `HarfBuzz 8.3.1.5`

## Windows rendering note

`AngleEgl` is not statically linked by this package. Avalonia on Windows commonly defaults to `AngleEgl, Software`, so removing `av_libglesv2.dll` may leave only software rendering available.

If you want to allow non-ANGLE backends instead of shipping `av_libglesv2.dll`, configure explicit rendering fallback order:

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

## Default behavior

When a matching template exists for the consuming project's `TargetFramework` and `RuntimeIdentifier`, the package:

- sets `AppHostSourcePath`
- sets `SingleFileHostSourcePath`
- injects the Avalonia native-library resolver module initializer
- suppresses SkiaSharp/HarfBuzz native runtime copy

By default this package removes these native runtime files from publish output when active:

- `libSkiaSharp.dll`
- `libHarfBuzzSharp.dll`
- `libSkiaSharp.so`
- `libHarfBuzzSharp.so`

## Advanced Options

### Keep SkiaSharp native runtime files in publish output

```xml
<PropertyGroup>
  <DisableSkiaHarfBuzzRuntimeCopy>false</DisableSkiaHarfBuzzRuntimeCopy>
</PropertyGroup>
```

### Disable Avalonia designer detection

If you only want to use SkiaSharp, you can disable Avalonia designer environment detection with the following property to avoid taking a dependency on Avalonia:

```xml
<PropertyGroup>
  <DetectAvaloniaDesigner>false</DetectAvaloniaDesigner>
</PropertyGroup>
```
