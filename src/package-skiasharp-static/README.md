# ChsBuffer.SkiaSharp.Static

Static SkiaSharp and HarfBuzzSharp libraries for NativeAOT. Reference the meta
package alongside Avalonia; graphics package dependencies are selected automatically:

```xml
<PropertyGroup>
  <PublishAot>true</PublishAot>
</PropertyGroup>
<ItemGroup>
  <PackageReference Include="Avalonia.Desktop" Version="12.1.6" />
  <PackageReference Include="ChsBuffer.SkiaSharp.Static" Version="3.119.4.1" />
</ItemGroup>
```

Publish with an explicit RID, for example `dotnet publish -c Release -r linux-x64`.
Use version `4.157.0.2` for SkiaSharp 4. A single architecture can instead reference
`ChsBuffer.SkiaSharp.Static.<RID>`.

Both versions support `win-x64`, `win-arm64`, `linux-x64`, `linux-arm64`,
`linux-musl-x64`, `linux-musl-arm64`, `osx-x64` and `osx-arm64`.

| Static package | Minimum managed SkiaSharp | Minimum managed HarfBuzzSharp |
| --- | --- | --- |
| 3.119.4 | 3.119.4 | 8.3.1.5 |
| 4.157.0 | 4.157.0 | 14.4.0.200 |

SkiaSharp and HarfBuzzSharp bring their Windows and macOS native libraries themselves.
The `linux-*` RID packages also depend on `SkiaSharp.NativeAssets.Linux` and
`HarfBuzzSharp.NativeAssets.Linux` of these versions, so the Linux libraries match the
managed packages instead of staying at the versions Avalonia depends on.

Only NativeAOT with a matching RID activates the integration. The targets add
`NativeLibrary`, `DirectPInvoke` and system link inputs, and remove corresponding
dynamic graphics libraries before publishing. Linux archives are grouped to resolve
mutual dependencies. Payloads, source manifests, GN arguments and licenses are under
`build/native/<rid>/`.

The archives contain Skia, HarfBuzz and the third-party libraries that the build
compiles into them, which differ by version and platform (for example libpng,
libjpeg-turbo, libwebp, expat, zlib and wuffs; FreeType on Linux). The license files
of each component are under `build/native/<rid>/licenses/<component>/`, and the source
manifest records their SHA-256. `THIRD-PARTY-NOTICES.txt`, the package license,
reproduces them together with the libc++ license where it applies and the license of
the package's MSBuild files. Executables that link the archives contain this code, and
these licenses ask binary distributions to reproduce their notices.

Windows libraries are compiled with Control Flow Guard (`/guard:cf`), and the `win-x64`
libraries also with EH continuation metadata (`/guard:ehcont`), so they link into
executables that require it: NativeAOT with `ControlFlowGuard=Guard`, which links with
`/guard:ehcont` on x64, and the relinked hosts of `ChsBuffer.AppHost.Static`.

Conflicting native lines fail with `HFG0001`; incompatible managed SkiaSharp
major.minor or HarfBuzzSharp major.minor.patch fail with `HFG0002` or `HFG0003`.
An incomplete selected payload fails with `HFG0005`. `HFG1001` warns when an AOT
build selects a nonempty Windows, Linux or macOS RID without a matching payload.
Builds without a RID or without NativeAOT remain inactive and silent, and load the
dynamic libraries of the native asset packages.

Linux requires system fontconfig and libstdc++. SkiaSharp 4 also bundles static
libc++ with its [LLVM license](https://github.com/llvm/llvm-project/blob/llvmorg-20.1.8/libcxx/LICENSE.TXT).
The native archives use Ubuntu 18.04 (GLIBC 2.27) or Alpine 3.17 sysroots. Publish
with the matching sysroot to preserve that ABI baseline; publishing on a newer
system can raise the final executable's requirements. Avalonia 12 on Linux should
request Vulkan API 1.3 when using SkiaSharp 4.

macOS links system libc++, AppKit and the required Apple graphics frameworks.
Metal and OpenGL are enabled. SkiaSharp 3.119.4 archives target macOS 10.13 on x64
and 11.0 on arm64; SkiaSharp 4.157.0 archives target macOS 12.0, as upstream does.
The final application must also satisfy .NET and Avalonia's OS requirements. ANGLE on Windows and AvaloniaNative on macOS are separate packages.
