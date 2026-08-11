# ChsBuffer.Avalonia.AppHost

Apphost and single-file host templates for Avalonia, prelinked against ANGLE, SkiaSharp, and HarfBuzz native libraries, for **.NET 10.0** on Windows and Linux.

This package replaces the SDK apphost templates for supported Avalonia publish targets and removes the corresponding native runtime files from publish output by default.

Supported template RIDs:

- `win-x64`
- `win-arm64`
- `linux-x64`

## Default behavior

When a matching template exists for the consuming project's `TargetFramework` and `RuntimeIdentifier`, the package:

- sets `AppHostSourcePath`
- sets `SingleFileHostSourcePath`
- injects the Avalonia native-library resolver module initializer
- suppresses ANGLE/SkiaSharp/HarfBuzz native runtime copy

By default this package removes these native runtime files from publish output when active:

- `libSkiaSharp.dll`
- `libHarfBuzzSharp.dll`
- `av_libglesv2.dll`
- `libSkiaSharp.so`
- `libHarfBuzzSharp.so`

## Advanced Options

### Keep SkiaSharp native runtime files in publish output

```xml
<PropertyGroup>
  <DisableSkiaHarfBuzzRuntimeCopy>false</DisableSkiaHarfBuzzRuntimeCopy>
</PropertyGroup>
```

### Keep ANGLE native runtime files in publish output

```xml
<PropertyGroup>
  <DisableAngleRuntimeCopy>false</DisableAngleRuntimeCopy>
</PropertyGroup>
```

### Disable Avalonia designer detection

If you only want to use SkiaSharp, you can disable Avalonia designer environment detection with the following property:

```xml
<PropertyGroup>
  <DetectAvaloniaDesigner>false</DetectAvaloniaDesigner>
</PropertyGroup>
```

### Disable the Avalonia.Win32 import resolver

If the application does not reference `Avalonia.Win32`, or manages its resolver itself, opt out at compile time:

```xml
<PropertyGroup>
  <DefineConstants>$(DefineConstants);HOSTFORGE_DISABLE_AVALONIA_WIN32_IMPORT_RESOLVER</DefineConstants>
</PropertyGroup>
```
