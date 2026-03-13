# ChsBuffer.Avalonia.AppHost

Apphost and single-file host templates for Avalonia, prelinked against SkiaSharp and HarfBuzz native libraries, for **.NET 10.0** on Windows and Linux.

This package replaces the SDK apphost templates for supported Avalonia publish targets and suppresses redundant SkiaSharp/HarfBuzz native runtime copy by default.

Supported template RIDs:

- `win-x64`
- `win-arm64`
- `linux-x64`

## Package version compatibility

- `11.0.0`: supports Avalonia 11 and is prelinked against `SkiaSharp 2.88.9` + `HarfBuzz 7.3.0`
- `12.0.0`: supports Avalonia 12 and is prelinked against `SkiaSharp 3.119.2` + `HarfBuzz 8.3.1`

## Windows rendering note

`AngleEgl` is not statically linked by this package. Avalonia 11 Win32 commonly defaults to `AngleEgl, Software`, so removing `av_libglesv2.dll` may leave only software rendering available.

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

By default this package removes these native runtime files from build/publish output when active:

- `libSkiaSharp.dll`
- `libHarfBuzzSharp.dll`
- `libSkiaSharp.so`
- `libHarfBuzzSharp.so`

You can restore default SDK copy behavior with:

```xml
<PropertyGroup>
  <DisableSkiaHarfBuzzRuntimeCopy>false</DisableSkiaHarfBuzzRuntimeCopy>
</PropertyGroup>
```
