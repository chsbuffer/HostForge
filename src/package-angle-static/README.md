# ChsBuffer.Angle.Static

Static ANGLE libraries for Windows NativeAOT, supporting `win-x64` and `win-arm64`:
ANGLE is linked into the executable instead of `av_libglesv2.dll` being published
next to it. The meta package includes both RIDs; use `ChsBuffer.Angle.Static.<RID>`
to restore only one architecture.

```xml
<PropertyGroup>
  <PublishAot>true</PublishAot>
</PropertyGroup>
<ItemGroup>
  <PackageReference Include="ChsBuffer.Angle.Static" Version="2.1.27548.2" />
</ItemGroup>
```

Publish with `dotnet publish -c Release -r win-x64` or `win-arm64`. This package
supports Avalonia's `AngleEgl` renderer and works with either supported static
SkiaSharp version. Builds without NativeAOT or without a Windows RID load the
dynamic library as usual.
