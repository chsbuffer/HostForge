# ChsBuffer.Angle.Static

Static ANGLE libraries for Windows NativeAOT, supporting `win-x64` and `win-arm64`.
The meta package includes both RIDs; use `ChsBuffer.Angle.Static.<RID>` to restore
only one architecture.

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
SkiaSharp version. Linux and macOS use their platform graphics integrations.

For a matching Windows AOT RID, `buildTransitive` adds `libANGLE_static.lib`,
`libGLESv2_static.lib`, `DirectPInvoke` names and system libraries, and filters
`av_libglesv2.dll` from publishing. The archives and `av_libglesv2.def` are under
`build/native/<rid>/`.

The archives contain ANGLE and the third-party code that its libraries link. The
license file of each component is under `build/native/<rid>/licenses/<component>/`.
`THIRD-PARTY-NOTICES.txt`, the package license, reproduces them together with the
license of the package's MSBuild files. Executables that link the archives contain
this code, and these licenses ask binary distributions to reproduce their notices.

An incomplete selected payload fails with `HFG0005`. A Windows AOT build with a
nonempty unsupported RID warns with `HFG1001`. Other platforms, builds without a
RID and non-AOT projects remain inactive and silent.
