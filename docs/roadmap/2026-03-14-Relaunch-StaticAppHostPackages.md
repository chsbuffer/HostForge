# 重启静态 .NET 宿主包

## 目标

- `Windows x64`

## 背景

当前已发布的宿主模板只有 `ChsBuffer.Avalonia.AppHost`，用于从 Avalonia 应用发布结果中移除 `libSkiaSharp.dll` 和 `libHarfBuzzSharp.dll`。

当前有两项变化：

1. 现有模板只覆盖 `SkiaSharp` / `HarfBuzz`，已有继续消除更多原生 DLL 的需求；
2. `StaticAppHost` 在补上 Linux 支持后，源码级集成和消费体验已经改善。

因此，本次工作恢复 `StaticAppHost` 的 Windows 包发布推进。当前前提是先处理 host 产物对特定工具链的依赖。

## 当前阻塞

### 1. singlefilehost 构建仍受全程序优化影响

`.NET Host` 当前的 Windows 原生构建会启用全程序优化（MSVC 下对应 `/GL`，CMake 侧入口是 `CMAKE_INTERPROCEDURAL_OPTIMIZATION`）。

对 `StaticAppHost` 来说，这会增加 host 产物对特定工具链和链接行为的依赖，不利于稳定复用。

当前需要一套关闭全程序优化的 host 输入产物。

```powershell
msbuild .\src\coreclr\runtime-prereqs.proj /t:BuildPrereqs /p:TargetOS=windows /p:TargetArchitecture=x64 /p:Configuration=Release
.\src\coreclr\build-runtime.cmd -x64 -release -os windows -configureonly -cmakeargs "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF" [-subdir singlefilehost]
ninja -C .\artifacts\obj\coreclr\windows.x64.Release singlefilehost
```

```powershell
.\build.cmd -subset clr.runtime -c Release -a x64 /p:ConfigureOnly=true /p:CMakeArgs=-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF /p:NoPgoOptimize=true [/p:BuildSubdirectory=singlefilehost]
ninja -C .\artifacts\obj\coreclr\windows.x64.Release singlefilehost
```

一条命令构建

```powershell
.\build.cmd -subset clr.runtime -c Release -a x64 /p:CMakeArgs=-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF /p:NoPgoOptimize=true [/p:BuildSubdirectory=singlefilehost]
```

后续需要验证：

预计关闭优化会导致 启动、JIT 本身的编译速度、GC/loader/helper 等热路径发生性能回退，对启动敏感、JIT 密集、runtime 开销占比高的应用影响会更明显。

### 2. apphost 构建仍受全程序优化影响

host.native 这条 Windows 路径里， /p:CMakeArgs=... 基本没传进去：
corehost.proj:147 -> src/native/corehost/build.cmd Windows 分支没有 $(CMakeArgs) 没把 CMakeArgs 传下去

最直接的无 patch 做法是：先用 canonical 的 build.cmd -subset host.native 只做 configure，然后你自己补一次 CMake cache，再单独编 apphost。
```
.\build.cmd -subset host.native -c Release -a x64 /p:ConfigureOnly=true

cmake -S .\src\native\corehost -B .\artifacts\obj\win-x64.Release\corehost `
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF

ninja -C .\artifacts\obj\win-x64.Release\corehost apphost
```

### 3. 已发布的 `ChsBuffer.Avalonia.AppHost` 没有关闭全程序优化

当前已发布的 `ChsBuffer.Avalonia.AppHost` 使用的 host 模板没有关闭全程序优化。
`Avalonia AppHost` 保持目前开启优化的状态，`StaticAppHost` 包则必须关闭全程序优化，这将增加 workflow 的复杂性。

### 4. targets 需要按路径分支做行为分析和系统测试

当前 targets 的行为不是单一路径，至少需要覆盖以下维度：

- 不同 Host OS 路径；
- 不同 TFM 路径；
- 不同 RID 路径；
- `PublishAot=true`；
- `PublishAot=false`；
- build / publish；
- apphost / singlefilehost。

这些路径需要分别做两类工作：

1. 行为分析，确认每条路径上 SDK 会调用哪些 target、使用哪些输入、回写哪些输出；
2. 系统测试，确认不同路径下链接结果、增量行为和消费端体验一致。


## 附带交付：`ChsBuffer.SkiaSharp.Static`

本次工作包含 `ChsBuffer.SkiaSharp.Static` 的整理和发布。

现有的 `2ndLAB.SkiaSharp.Static` 有两个问题：

1. `HarfBuzzSharp` 的本机构建库没有关闭全程序优化；
2. targets 目前只在 `PublishAot` 场景下启用。

如果继续沿用现有包，`StaticAppHost` 在工具链假设和消费方式上无法完全对齐。
