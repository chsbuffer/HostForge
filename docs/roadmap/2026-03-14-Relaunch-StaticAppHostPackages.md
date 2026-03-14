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

### 1. host 构建仍受全程序优化影响

`.NET Host` 当前的 Windows 原生构建会启用全程序优化（MSVC 下对应 `/GL`，CMake 侧入口是 `CMAKE_INTERPROCEDURAL_OPTIMIZATION`）。

对 `StaticAppHost` 来说，这会增加 host 产物对特定工具链和链接行为的依赖，不利于稳定复用。

当前需要一套关闭全程序优化的 host 输入产物。

### 2. 常规 `build.cmd` 流程会被 PGO 检查卡住

目前已确认，可以在 `eng\native\configurecompiler.cmake` 中关闭：

```cmake
set(CMAKE_INTERPROCEDURAL_OPTIMIZATION ON)
```

从而去掉编译阶段的 `/GL`。

但继续使用 `build.cmd clr.native` 时，流程仍会被 `pgocheck.py` 拦住。失败原因不是 `singlefilehost` 本身编译不过，而是流程仍会检查 `coreclr.dll`、`clrjit.dll` 等运行时组件是否启用了 PGO。

因此，当前问题不仅是关闭 `/GL`，还包括在不触发整条 runtime 校验链路的前提下只构建需要的 host 产物。

## 当前可行但待验证的替代方案

当前有一条可行的构建路径，可以直接生成 `singlefilehost.exe`，且不会触发 `pgocheck.py` 的失败：

```powershell
msbuild src\native\corehost\corehost.proj /t:GenerateRuntimeVersionFile
.\src\coreclr\build-runtime.cmd -x64 -release -os windows -configureonly -component runtime
ninja -C .\artifacts\obj\coreclr\windows.x64.Release singlefilehost.exe
```

后续需要验证：

1. 这条路径产出的 `singlefilehost.exe` 是否与预期的正式 host 输入一致；
2. 关闭 `/GL` 后，对应用的运行速度有多少影响；

### 3. 已发布的 `ChsBuffer.Avalonia.AppHost` 没有关闭全程序优化

当前已发布的 `ChsBuffer.Avalonia.AppHost` 使用的 host 模板没有关闭全程序优化。

新包如果改为关闭全程序优化，需要明确 `Avalonia AppHost` 和 `StaticAppHost` 是否共用同一套 host 构建产物。

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
