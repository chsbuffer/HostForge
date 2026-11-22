# ChsBuffer.Avalonia.AppHost.win-x64

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10 and .NET 11 `win-x64`, with ANGLE, SkiaSharp, and HarfBuzzSharp native libraries statically linked.

Like the official .NET hosts, the templates use Control Flow Guard, are CET compatible and carry an EH continuation table.

`THIRD-PARTY-NOTICES.txt`, the package license, reproduces the licenses of the code linked into the templates: the .NET runtime of each template with its third-party notices, Skia, HarfBuzz and the third-party libraries in their archives, and ANGLE with the third-party code it links. Executables published with the templates contain this code, and these licenses ask binary distributions to reproduce their notices.

This package brings in the shared Build integration automatically. Use `ChsBuffer.Avalonia.AppHost` instead to include every supported RID.
