# AvaloniaNative

Build a single-architecture static archive from the pinned Avalonia source with
Conan 2, uv, .NET 10 and Xcode 26.0.1:

```sh
DEVELOPER_DIR=/Applications/Xcode_26.0.1.app/Contents/Developer task build-avalonianative RID=osx-arm64 AVALONIA_VERSION=12.1.3
task pack-avalonianative RID=osx-arm64 AVALONIA_VERSION=12.1.3
```

Use `RID=osx-x64` for Intel macOS. Deployment goes to
`artifacts/avalonianative/<version>/<rid>/`; archives target macOS 12.0.
Run `task test-avalonianative` after packing to check integration and render text.
