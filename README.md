# HostForge

**ChsBuffer.Avalonia.AppHost**: [![NuGet.org](https://img.shields.io/nuget/v/ChsBuffer.Avalonia.AppHost.svg?logo=nuget)](https://www.nuget.org/packages/ChsBuffer.Avalonia.AppHost/)

## 项目简介

本项目聚焦一种不依赖 NativeAOT 的“真单文件”发布方案。  
当应用包含本机库依赖时，通过重链接 AppHost / SingleFileHost，将静态库直接并入宿主，避免 `IncludeNativeLibrariesForSelfExtract` 的解包模式。

项目同时覆盖两条落地路径：

- 面向通用 .NET 应用的 `StaticAppHost` 构建集成。
- 面向 Avalonia 11 的 `Avalonia AppHost`（预链接 SkiaSharp 2.88.9 的宿主模板）。

当前重点是 `win-x64`，并配套可验证的构建、打包与测试流水线。

## 核心目标

- 基于 runtime 源码产出可复用的 Host 静态库（apphost/singlefilehost）。
- 提供 `StaticAppHost.targets` 源码级集成，在常规 .NET 构建/发布流程中自动重链接 Host。
- 提供 `Avalonia AppHost` NuGet 包，面向 Avalonia 11 提供可直接消费的宿主模板与初始化逻辑。
- 支持增量缓存验证（首次触发链接、二次命中缓存）。

## 目录说明

- `docs/roadmap/2026-03-06-ProjectLaunch.md`：项目启动的设计说明。
- `repo/runtime-<version>`：dotnet/runtime 源码（上游源码输入）。
- `repo/skia-<version>`：mono/skia 源码（用于构建静态 Skia/HarfBuzz 库）。
- `repo/deps-mirror`：依赖仓库的本地裸仓库缓存（仅非 CI）。
- `repo/patch`：本仓库对上游工程使用的补丁与参数文件。
- `scripts/pipeline.py`：主流水线入口（HostLib 构建 -> 打包 -> 矩阵测试）。
- `scripts/build-hostlibs.py`：构建 Host 静态库到 `artifacts/hostlibs/<version>/<rid>`。
- `scripts/build-skia-harfbuzz.py`：构建并收集 mono/skia + HarfBuzz 静态库到 `artifacts/skiasharp/<version>/<rid>`。
- `scripts/init-vs-env.cmd`：初始化 Visual Studio/MSVC 构建环境。
- `scripts/set-cmake-path.ps1`：设置 CMake 相关路径辅助脚本。
- `src/StaticAppHost.targets`、`src/StaticAppHost.Windows.targets`、`src/findvcvarsall.bat`：StaticAppHost 源码级 MSBuild 集成。
- `src/package-avalonia-apphost`：`ChsBuffer.Avalonia.AppHost` 包工程。
- `samples/simple-pinvoke`：消费端示例工程（dllexport/pinvoke 最小验证）。
- `samples/avalonia-sample`：Avalonia 消费端示例工程（用于验证 Avalonia AppHost 包）。
- `tests/HostForge.TestInfra`：测试共享基础设施（命令执行、工作区构建、断言工具）。
- `tests/HostForge.AvaloniaAppHost.Tests`：Avalonia AppHost 构建/发布行为的 TUnit 集成测试。
- `tests/HostForge.StaticAppHost.Tests`：Static AppHost 增量链接矩阵的 TUnit 集成测试。
- `artifacts/hostlibs/<version>/<rid>`：Host 静态库输出目录。
- `artifacts/skiasharp/<version>/<rid>`：SkiaSharp/HarfBuzz 静态库输出目录。

## 环境要求

- Windows 11
- MSVC Build Tools 14.50 (VS 2026) x64,arm64
- Windows SDK 10.0.26100
- Python 3.12+
- Ninja build
- CMake on Windows
- LLVM (C:\Program Files\LLVM)
- .NET SDK 10.0.101 (可选)

## 逐步生成

- 检出外部依赖
```powershell
python .\scripts\checkout-deps.py runtime 10.0
python .\scripts\checkout-deps.py skiasharp 2.88.9
```

- 构建 HostLib：

```powershell
python .\scripts\pipeline.py hostlibs -v
```
或
```powershell
python .\scripts\build-hostlibs.py all -v --arch x64
python .\scripts\build-hostlibs.py all -v --arch arm64
```

- 运行构建集成矩阵测试：

```powershell
python .\scripts\pipeline.py matrix
```

- 构建 SkiaSharp：

```powershell
python .\scripts\pipeline.py skia -v
```
或
```powershell
python .\scripts\build-skia-harfbuzz.py -v -a x64
python .\scripts\build-skia-harfbuzz.py -v -a arm64
```

- 打包 Avalonia Host 包：

```powershell
python .\scripts\pipeline.py pack-avalonia -v
```

## 逐步生成 （SkiaSharp 3，未测试）

- 检出外部依赖
```powershell
python .\scripts\checkout-deps.py runtime 10.0
python .\scripts\checkout-deps.py skiasharp 3.119.2
```

- 构建 HostLib：

同上

- 构建 SkiaSharp：

```powershell
python .\scripts\build-skia-harfbuzz.py -v -a x64 --version 3.119.2
python .\scripts\build-skia-harfbuzz.py -v -a arm64 --version 3.119.2
```

- 打包 Avalonia Host 包：

```powershell
python .\scripts\pipeline.py pack-avalonia -v --target 12.0
```

- 运行 Avalonia 12 示例项目

```powershell
dotnet run --project .\samples\avalonia-sample\AvaloniaSample.csproj -c Release -p:TargetAvaloniaVersion=11.0
```

## 实验性 Linux 生成

- 构建 HostLib：

```bash
python scripts/build-hostlibs.py -v --os linux --arch x64
```

- 运行构建集成矩阵测试：

```bash
dotnet run --project samples/simple-pinvoke/SimplePInvoke.csproj
dotnet publish samples/simple-pinvoke/SimplePInvoke.csproj -p:"PublishTrimmed=true"
```
