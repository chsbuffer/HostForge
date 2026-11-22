ChsBuffer.Avalonia.AppHost
==========================

Apphost and single-file host templates for Avalonia, prelinked against ANGLE, SkiaSharp and HarfBuzz native libraries, for .NET 10.0 and .NET 11.0 on Windows, Linux and Alpine Linux.

Supported template targets
--------------------------

- Avalonia 12
- .NET 10.0 (runtime 10.0.15)
- .NET 11.0 (runtime 11.0.0)
- win-x64
- win-arm64
- linux-x64
- linux-musl-x64
- linux-musl-arm64

Compatibility and fallback
--------------------------

- net10.0 / net11.0 + win-x64: active
- net10.0 / net11.0 + win-arm64: active
- net10.0 / net11.0 + linux-x64: active
- net10.0 / net11.0 + linux-musl-x64: active
- net10.0 / net11.0 + linux-musl-arm64: active
- OS-specific TFMs (e.g. net11.0-windows) use the template of their .NET version
- builds without a runtime identifier: inactive, without a warning
- NativeAOT (PublishAot=true) and UseAppHost=false: inactive, the dynamic native libraries are published
- C# projects only; F# and Visual Basic projects stay inactive
- unsupported target framework & runtime identifier combinations: inactive fallback

Learn more
----------

README on GitHub:
https://github.com/chsbuffer/HostForge/tree/main/src/package-avalonia-apphost#readme
