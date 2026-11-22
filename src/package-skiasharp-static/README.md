# ChsBuffer.SkiaSharp.Static

Static SkiaSharp and HarfBuzzSharp libraries for NativeAOT: the libraries are linked
into the executable instead of being published next to it. Reference the package
alongside Avalonia:

```xml
<PropertyGroup>
  <PublishAot>true</PublishAot>
</PropertyGroup>
<ItemGroup>
  <PackageReference Include="Avalonia.Desktop" Version="12.1.6" />
  <PackageReference Include="ChsBuffer.SkiaSharp.Static" Version="3.119.4.2" />
</ItemGroup>
```

Publish with an explicit RID, for example `dotnet publish -c Release -r linux-x64`.
Use version `4.157.0.3` for SkiaSharp 4. A single architecture can instead reference
`ChsBuffer.SkiaSharp.Static.<RID>`. Builds without NativeAOT or without a RID load
the dynamic libraries as usual.

Both versions support `win-x64`, `win-arm64`, `linux-x64`, `linux-arm64`,
`linux-musl-x64`, `linux-musl-arm64`, `osx-x64` and `osx-arm64`.

| Static package | Minimum managed SkiaSharp | Minimum managed HarfBuzzSharp |
| --- | --- | --- |
| 3.119.4 | 3.119.4 | 8.3.1.5 |
| 4.157.0 | 4.157.0 | 14.4.0.200 |

## Requirements

- Linux: fontconfig and libstdc++. The archives target glibc 2.27 (Ubuntu 18.04) or
  Alpine 3.17; publishing on a newer system can raise the executable's requirements.
  With SkiaSharp 4, Avalonia 12 should request Vulkan API 1.3.
- macOS: SkiaSharp 3.119.4 targets macOS 10.13 on x64 and 11.0 on arm64; SkiaSharp
  4.157.0 targets macOS 12.0.

ANGLE on Windows and AvaloniaNative on macOS are separate packages:
`ChsBuffer.Angle.Static` and `ChsBuffer.Avalonia.Native.Static`.
