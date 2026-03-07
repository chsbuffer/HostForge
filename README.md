# HostForge

## 项目简介

本项目聚焦一种不依赖 NativeAOT 的“真单文件”发布方案。  
当应用包含本机库依赖时，通过重链接 AppHost / SingleFileHost，将静态库直接并入宿主，避免 `IncludeNativeLibrariesForSelfExtract` 的解包模式。

项目同时覆盖两条落地路径：

- 面向通用 .NET 应用的 `StaticAppHost`。
- 面向 Avalonia 11 的 `Avalonia AppHost`（预链接 SkiaSharp/HarfBuzz 宿主模板）。

当前重点是 `win-x64`，并配套可验证的构建、打包与测试流水线。

## 核心目标

- 基于 runtime 源码产出可复用的 Host 静态库（apphost/singlefilehost）。
- 提供 `StaticAppHost` NuGet 包，在常规 .NET 构建/发布流程中自动重链接 Host。
- 提供 `Avalonia AppHost` NuGet 包，面向 Avalonia 11 提供可直接消费的宿主模板与初始化逻辑。
- 支持增量缓存验证（首次触发链接、二次命中缓存）。

## 目录说明

- `docs/roadmap/2026-03-06-ProjectLaunch.md`：项目启动的设计说明。
- `repo/runtime`：dotnet/runtime 源码（上游源码输入）。
- `repo/skia`：mono/skia 源码（用于构建静态 Skia/HarfBuzz 库）。
- `repo/depot_tools`：Chromium `depot_tools` 工具链（用于 `gn`/`ninja`/依赖同步）。
- `repo/patch`：本仓库对上游工程使用的补丁与参数文件。
- `scripts/pipeline.py`：主流水线入口（HostLib 构建 -> 打包 -> 矩阵测试）。
- `scripts/build-hostlibs.py`：构建 Host 静态库到 `artifacts/hostlibs/<rid>`。
- `scripts/build-skia-harfbuzz.py`：构建并收集 mono/skia + HarfBuzz 静态库到 `artifacts/skiasharp-2.88.9/<rid>`。
- `scripts/init-vs-env.cmd`：初始化 Visual Studio/MSVC 构建环境。
- `scripts/set-cmake-path.ps1`：设置 CMake 相关路径辅助脚本。
- `src/package-static-apphost`：`ChsBuffer.NETCore.StaticAppHost.win-x64` 包工程。
- `src/package-avalonia-apphost`：`ChsBuffer.Avalonia.AppHost` 包工程。
- `samples/simple-pinvoke`：消费端示例工程（dllexport/pinvoke 最小验证）。
- `samples/avalonia-sample`：Avalonia 消费端示例工程（用于验证 Avalonia AppHost 包）。
- `tests/HostForge.TestInfra`：测试共享基础设施（命令执行、工作区构建、断言工具）。
- `tests/HostForge.AvaloniaAppHost.Tests`：Avalonia AppHost 构建/发布行为的 TUnit 集成测试。
- `tests/HostForge.StaticAppHost.Tests`：Static AppHost 增量链接矩阵的 TUnit 集成测试。
- `artifacts/hostlibs/<rid>`：Host 静态库输出目录。
- `artifacts/skiasharp-2.88.9/<rid>`：SkiaSharp/HarfBuzz 静态库输出目录。

## 环境要求

- Windows
- MSVC 工具链
- .NET SDK 10.x
- Python 3.12+

## 快速开始

1. 一键执行完整流水线（推荐，Python）：

```powershell
python .\scripts\pipeline.py all
```

2. 若仅验证打包与矩阵（跳过 HostLib 编译）：

```powershell
python .\scripts\pipeline.py all --skip-host-lib-build
```

3. 仅构建 HostLib（不跑打包和测试）：

```powershell
python .\scripts\pipeline.py hostlibs -a x64
```

### 跳过测试命令

- 跳过矩阵测试（执行 HostLib 构建 + 打包）：

```powershell
python .\scripts\pipeline.py all --skip-matrix-test
```

- 仅打包（不构建 HostLib、不跑测试）：

```powershell
python .\scripts\pipeline.py all --skip-host-lib-build --skip-matrix-test
```

- 仅运行矩阵测试：

```powershell
python .\scripts\pipeline.py matrix -a x64 -c Release
```

- 仅打包 Avalonia Host 包：

```powershell
python .\scripts\pipeline.py pack-avalonia -a x64 -c Release
```

- 仅打包 Static Host 包：

```powershell
python .\scripts\pipeline.py pack-static -a x64 -c Release
```
