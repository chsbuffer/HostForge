# ChsBuffer.Avalonia.AppHost

Prelinked **Avalonia 11** apphost/singlefilehost template package for **.NET 10.0** on **Windows `win-x64` + `win-arm64`**.

## Important first

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

## What this package provides

- `template/net10.0/win-x64/apphost.exe`
- `template/net10.0/win-x64/singlefilehost.exe`
- `template/net10.0/win-arm64/apphost.exe`
- `template/net10.0/win-arm64/singlefilehost.exe`
- `buildTransitive/ChsBuffer.Avalonia.AppHost.targets`
  - sets `AppHostSourcePath` and `SingleFileHostSourcePath` from the package template path
  - suppress SkiaSharp/HarfBuzz native runtime copy
- `contentFiles/cs/net10.0/ModuleInitializer.cs`
  - maps Avalonia native DLL names to the main program handle

## NativeAssets behavior

By default this package removes these files from build/publish output:

- `libSkiaSharp.dll`
- `libHarfBuzzSharp.dll`

You can switch back to default SDK copy behavior by set this in your app project:

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
