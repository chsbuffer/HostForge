# ChsBuffer.Avalonia.AppHost.win-arm64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 and .NET 11 `win-arm64`, with ANGLE, SkiaSharp, and HarfBuzzSharp native libraries statically linked.

`THIRD-PARTY-NOTICES.txt` in the package reproduces the licenses of the code linked into the templates: the .NET runtime of each template with its third-party notices, Skia, HarfBuzz and the third-party libraries in their archives, and ANGLE with the third-party code it links. Executables published with the templates contain this code, and these licenses ask binary distributions to reproduce their notices.

In projects that target .NET 10 or later, this package brings in the shared Build integration automatically; it declares no dependencies for earlier .NET versions, which keep the SDK host without a warning. Use `ChsBuffer.Avalonia.AppHost` instead to include every supported RID.
