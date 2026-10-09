# HostForge static graphics

Reference `ChsBuffer.SkiaSharp.Static`, `ChsBuffer.Angle.Static` and
`ChsBuffer.Avalonia.Native.Static` alongside Avalonia. Publish with
`PublishAot=true` and an explicit runtime identifier.

```xml
<PropertyGroup>
  <PublishAot>true</PublishAot>
</PropertyGroup>
<ItemGroup>
  <PackageReference Include="Avalonia.Desktop" Version="12.1.3" />
  <PackageReference Include="ChsBuffer.SkiaSharp.Static" Version="3.119.4" />
  <PackageReference Include="ChsBuffer.Angle.Static" Version="2.1.27548.20260419" />
  <PackageReference Include="ChsBuffer.Avalonia.Native.Static" Version="12.1.3" />
</ItemGroup>
```

Run `dotnet publish -c Release -r <RID>`. Change only the SkiaSharp static package
version to `4.153.1` to use SkiaSharp 4.

The SkiaSharp meta package selects compatible managed SkiaSharp and HarfBuzzSharp
versions through its RID package dependencies. No additional managed graphics
package references are required. Only the matching RID contributes native inputs.
ANGLE applies to Windows; Avalonia.Native applies to macOS.

SkiaSharp 3.119.4 and 4.153.1 each include eight RIDs: `win-x64`, `win-arm64`,
`linux-x64`, `linux-arm64`, `linux-musl-x64`, `linux-musl-arm64`, `osx-x64` and
`osx-arm64`. ANGLE includes both Windows RIDs; Avalonia.Native 12.1.3 includes
both macOS RIDs. Meta packages require the exact matching RID package versions.

All packages share the same build integration and error codes:

| Code | Condition |
| --- | --- |
| HFG0001 | Conflicting Skia native lines or multiple AvaloniaNative payloads for the selected RID |
| HFG0002 | Managed SkiaSharp major.minor differs from the static native line |
| HFG0003 | Managed HarfBuzzSharp major.minor.patch differs from the static native line |
| HFG0004 | Restored Avalonia/Avalonia.Native does not match the native archive version |
| HFG0005 | The selected native payload is incomplete |
| HFG1001 | An AOT build selects a nonempty RID within a referenced component's platform scope, but no payload matches |

Without a RID, outside the component's platform, or without NativeAOT, packages
remain inactive and emit no missing-payload warning.
