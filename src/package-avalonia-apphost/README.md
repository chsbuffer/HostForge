# ChsBuffer.Avalonia.AppHost

Prelinked Avalonia 12 apphost and single-file host templates for .NET 10, with the required native libraries statically linked.

## Package selection

| Package | Templates |
|---|---|
| `ChsBuffer.Avalonia.AppHost` | All supported RIDs |
| `ChsBuffer.Avalonia.AppHost.win-x64` | `win-x64` only |
| `ChsBuffer.Avalonia.AppHost.win-arm64` | `win-arm64` only |
| `ChsBuffer.Avalonia.AppHost.linux-x64` | `linux-x64` only |

Use the package without a suffix for all supported targets, or reference only the RID packages the project publishes. RID packages bring in the shared `ChsBuffer.Avalonia.AppHost.Build` integration automatically.

The templates activate when the project targets .NET 10 and publishes for an installed RID. Other target combinations keep the standard .NET SDK host behavior.
