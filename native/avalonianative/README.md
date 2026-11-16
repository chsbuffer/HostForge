# AvaloniaNative

Build a single-architecture static archive from the pinned Avalonia source with
Conan 2, uv, .NET 10 and Xcode 26.0.1:

```sh
DEVELOPER_DIR=/Applications/Xcode_26.0.1.app/Contents/Developer task build-avalonianative
task pack-avalonianative RID=osx-arm64
```

`AVALONIA_VERSION` defaults to `AvaloniaNativeVersion` in `Directory.Build.props`;
other values must have a source entry in [`conandata.yml`](conandata.yml).
`build-avalonianative` defaults to `RID=osx-arm64`; use `RID=osx-x64` for Intel macOS.
Deployment goes to `artifacts/avalonianative/<version>/<rid>/`; archives target macOS 12.0.
Run `task test-avalonianative` after packing to check integration and render text.
