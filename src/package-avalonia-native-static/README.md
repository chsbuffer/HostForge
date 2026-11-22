# ChsBuffer.Avalonia.Native.Static

Statically link AvaloniaNative into a macOS NativeAOT application. The meta package
includes both `osx-x64` and `osx-arm64`; use the corresponding RID package to
restore only one architecture.

```xml
<PropertyGroup>
  <PublishAot>true</PublishAot>
</PropertyGroup>
<ItemGroup>
  <PackageReference Include="Avalonia.Desktop" Version="12.1.6" />
  <PackageReference Include="ChsBuffer.Avalonia.Native.Static" Version="12.1.6.1" />
</ItemGroup>
```

Publish on macOS with `dotnet publish -c Release -r osx-arm64` (or `osx-x64`).
Keep `AvaloniaNativePlatformOptions.AvaloniaNativeLibraryPath` unset: that option
explicitly requests dynamic loading and bypasses Avalonia's default P/Invoke.
Builds without NativeAOT or without a macOS RID load `libAvaloniaNative.dylib` as
usual.

The restored `Avalonia` and `Avalonia.Native` versions must be exactly 12.1.6
(`HFG0004` otherwise); the fourth part of the package version is a HostForge
packaging revision. The archives target macOS 12.0; the application must also meet
the system requirements of its .NET and Avalonia versions.
