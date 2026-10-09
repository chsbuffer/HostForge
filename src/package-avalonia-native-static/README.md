# ChsBuffer.Avalonia.Native.Static

Statically link AvaloniaNative into a macOS NativeAOT application. The meta package
includes both `osx-x64` and `osx-arm64`; use the corresponding RID package to
restore only one architecture.

```xml
<PropertyGroup>
  <PublishAot>true</PublishAot>
</PropertyGroup>
<ItemGroup>
  <PackageReference Include="Avalonia.Desktop" Version="12.1.3" />
  <PackageReference Include="ChsBuffer.Avalonia.Native.Static" Version="12.1.3" />
</ItemGroup>
```

Publish on macOS with `dotnet publish -c Release -r osx-arm64` (or `osx-x64`).
Keep `AvaloniaNativePlatformOptions.AvaloniaNativeLibraryPath` unset: that option
explicitly requests dynamic loading and bypasses Avalonia's default P/Invoke.

Integration activates only for `PublishAot=true` and an exact matching RID. It
force-loads the archive to retain Objective-C categories, links Apple's system
frameworks and C++/Objective-C runtimes, and removes `libAvaloniaNative.dylib`
before publish copying or single-file bundling. SkiaSharp and HarfBuzzSharp are
independent dependencies; their official dynamic native assets remain usable.

The restored `Avalonia` and `Avalonia.Native` versions must match the archive's
upstream version exactly (`HFG0004` on mismatch). A macOS AOT build without a
matching RID package produces `HFG1001` and leaves dynamic assets intact.
Builds without a RID or targeting other platforms are silent and add no linking
inputs. A fourth package version segment denotes a HostForge packaging revision,
without changing the required Avalonia version. There is no additional font
initialization code.

Archives are built from Avalonia 12.1.3 source, commit
`8eeda4f6f546165b3f72e63c9f42247abb306905`, with upstream MicroCom-generated
headers. Each archive contains one architecture, targets macOS 12.0, and includes
a source/toolchain/SHA-256 manifest and the Avalonia license. The final
application must also meet the system requirements of its .NET and Avalonia
versions; the archive deployment target does not override those requirements.
