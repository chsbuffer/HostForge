# ChsBuffer.SkiaSharp.Static

Static SkiaSharp and HarfBuzzSharp libraries for NativeAOT. Reference the meta
package alongside Avalonia; managed graphics dependencies are selected automatically:

```xml
<PropertyGroup>
  <PublishAot>true</PublishAot>
</PropertyGroup>
<ItemGroup>
  <PackageReference Include="Avalonia.Desktop" Version="12.1.3" />
  <PackageReference Include="ChsBuffer.SkiaSharp.Static" Version="3.119.4" />
</ItemGroup>
```

Publish with an explicit RID, for example `dotnet publish -c Release -r linux-x64`.
Use version `4.153.1` for SkiaSharp 4. A single architecture can instead reference
`ChsBuffer.SkiaSharp.Static.<RID>`.

Both versions support `win-x64`, `win-arm64`, `linux-x64`, `linux-arm64`,
`linux-musl-x64`, `linux-musl-arm64`, `osx-x64` and `osx-arm64`.

| Static package | Minimum managed SkiaSharp | Minimum managed HarfBuzzSharp |
| --- | --- | --- |
| 3.119.4 | 3.119.4 | 8.3.1.5 |
| 4.153.1 | 4.153.1 | 14.2.1.301 |

Only NativeAOT with a matching RID activates the integration. The targets add
`NativeLibrary`, `DirectPInvoke` and system link inputs, and remove corresponding
dynamic graphics libraries before publishing. Linux archives are grouped to resolve
mutual dependencies. Payloads, source manifests, GN arguments and licenses are under
`build/native/<rid>/`.

Conflicting native lines fail with `HFG0001`; incompatible managed SkiaSharp
major.minor or HarfBuzzSharp major.minor.patch fail with `HFG0002` or `HFG0003`.
An incomplete selected payload fails with `HFG0005`. `HFG1001` warns when an AOT
build selects a nonempty Windows, Linux or macOS RID without a matching payload.
Builds without a RID or without NativeAOT remain inactive and silent.

Linux requires system fontconfig and libstdc++. SkiaSharp 4 also bundles static
libc++ with its [LLVM license](https://github.com/llvm/llvm-project/blob/llvmorg-20.1.8/libcxx/LICENSE.TXT).
The native archives use Ubuntu 18.04 (GLIBC 2.27) or Alpine 3.17 sysroots. Publish
with the matching sysroot to preserve that ABI baseline; publishing on a newer
system can raise the final executable's requirements. Avalonia 12 on Linux should
request Vulkan API 1.3 when using SkiaSharp 4.

macOS links system libc++, AppKit and the required Apple graphics frameworks.
Metal and OpenGL are enabled. Archive deployment targets are 10.13 for x64 and
11.0 for arm64; the final application must also satisfy .NET and Avalonia's OS
requirements. ANGLE on Windows and AvaloniaNative on macOS are separate packages.
