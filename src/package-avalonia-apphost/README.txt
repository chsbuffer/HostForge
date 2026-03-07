ChsBuffer.Avalonia.AppHost
==========================

Prelinked Avalonia 11 apphost and singlefilehost templates for .NET 10.0 on Windows.

Supported template targets
--------------------------

- Avalonia 11
- .NET 10.0
- win-x64
- win-arm64

Render mode notes
-----------------

AngleEgl is not statically linked by this package.

Avalonia 11 on Win32 commonly uses a default rendering mode equivalent to:

- AngleEgl
- Software

If av_libglesv2.dll is not present, the default configuration may leave only software rendering.

To avoid software-only fallback, explicitly include additional rendering backends in your app,
for example Vulkan or Wgl, in addition to AngleEgl and Software.

Example:

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

With that configuration, av_libglesv2.dll can be removed when a non-ANGLE backend is available
on the target machine.

Compatibility and fallback
--------------------------

- net10.0 + win-x64: active
- net10.0 + win-arm64: active
- unsupported target framework & runtime identifier combinations: inactive fallback

Learn more
----------

README on GitHub:
https://github.com/chsbuffer/HostForge/tree/main/src/package-avalonia-apphost#readme
