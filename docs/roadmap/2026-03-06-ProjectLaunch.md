## 概念

`.NET Host`：.NET 应用宿主，负责启动运行时（包括 JIT、GC 等组件）并调用托管入口点。

`AppHost`：默认 Host，会自动查找合适的已安装框架。适用场景包括：开发构建、依赖框架发布、依赖框架单文件发布、自包含发布。  
本文中，`AppHost` 有时会与 `SingleFileHost` 一起泛指 `.NET Host`，有时仅指默认 Host。主要原因是“依赖框架发布”长期是 .NET 生态主流形态，自包含和单文件需求是后续逐步扩展出来的。

`SingleFileHost`：静态链接运行时的 Host，主要用于自包含单文件发布。

`NativeAOT`：将托管代码预编译为原生代码的发布方式。它支持静态链接，与本项目目标有部分重叠，可借鉴其链路，但二者要解决的问题并不相同。

`ilc`：MSIL 到 obj 的编译器，是 `NativeAOT` 流水线/工具链的一部分。

## 项目目标

目标是：当应用依赖本机库时，在不使用 `NativeAOT` 的前提下，发布真正的单文件应用（而不是 `IncludeNativeLibrariesForSelfExtract` 这种“解包式单文件”）。

> [!NOTE]
> 如果你是 Avalonia 用户，且应用不大量依赖反射，并能接受更长发布时间，可参考：  
> https://github.com/peaceshi/Avalonia-NativeAOT-SingleFile

1. 让 `Avalonia` 开发者在安装本项目 `Avalonia AppHost` 包后自动替换 `AppHost`，摆脱 `Avalonia` 的本机动态链接库（angle/skiasharp/harfbuzz）。
2. 让 .NET 开发者在安装本项目 `Static AppHost` 包后，像 `NativeAOT` 一样通过设置 `NativeLibrary` `ItemProperty`，把静态库链接进 `AppHost`。

## 计划

- 第一阶段（当前）：打包 `win-x64`（含构建与发布流程）。
- 第二阶段：构建并发布 `win-arm64`。
- 未来：linux 和 loongarch，超级包（自动选择对应 RID 的包）。

## 前提：编译 AppHost 静态库

输入：

- build.py
- https://github.com/dotnet/runtime

产物：

- `libapphost_*.lib`：用于 *开发构建、依赖框架发布、依赖框架单文件发布、自包含发布*。
- `libsinglefilehost_*.lib`、`singlefilehost.def`：用于 *自包含单文件发布*。

## Avalonia AppHost 包

将 AppHost 静态库与 `artifacts/skiasharp-2.88.9/win-x64` 下的 Avalonia 11 静态库（SkiaSharp/HarfBuzz）链接，得到 `Avalonia` 的 Host。

`ChsBuffer.Avalonia.AppHost` NuGet 包包含：

- `buildTransitive/AvaloniaAppHost.props`：在 PropertyGroup 中添加 `AppHostSourcePath`、`SingleFileHostSourcePath`；在 ItemGroup 中添加 `Compile ModuleInitializer.cs`。
- `buildTransitive/AvaloniaAppHost.targets`：默认移除 `libSkiaSharp.dll` 和 `libHarfBuzzSharp.dll` 的复制；可通过属性开关恢复默认行为。
- `contentFiles/cs/net10.0/ModuleInitializer.cs`：设置 `DllImportResolver`。
- `template/net10.0/win-x64/apphost.exe`
- `template/net10.0/win-x64/singlefilehost.exe`

参考资料：

- https://github.com/dotnet/sdk/blob/main/src/Tasks/Microsoft.NET.Build.Tasks/targets/Microsoft.NET.Sdk.targets
- https://github.com/dotnet/sdk/blob/main/src/Tasks/Microsoft.NET.Build.Tasks/targets/Microsoft.NET.Publish.targets
- C:\Program Files\dotnet\sdk\10.0.103\Sdks\Microsoft.NET.Sdk\targets\Microsoft.NET.Sdk.targets
- C:\Program Files\dotnet\sdk\10.0.103\Sdks\Microsoft.NET.Sdk\targets\Microsoft.NET.Publish.targets
- ~\.nuget\packages\polyfill\9.8.1\build\Polyfill.targets
- https://learn.microsoft.com/en-us/nuget/create-packages/native-files-in-net-packages

## Static AppHost 包

`ChsBuffer.NETCore.StaticAppHost.win-x64` NuGet 包包含：

- `build/StaticAppHost.targets`：链接 `NativeLibrary` ItemGroup 生成 AppHost，并设置 PropertyGroup（与 Avalonia props 一致）。
- `build/findvcvarsall.bat`：供 targets 定位 MSVC Toolset。
- `build/win-x64/libapphost_obj.lib`
- `build/win-x64/libapphost_lib.lib`
- `build/win-x64/libapphost_directives.lib`
- `build/win-x64/libsinglefilehost_obj.lib`
- `build/win-x64/libsinglefilehost_lib.lib`
- `build/win-x64/libsinglefilehost_directives.lib`
- `build/win-x64/singlefilehost.def`

参考资料：

- ~\.nuget\packages\microsoft.dotnet.ilcompiler\10.0.3\build\findvcvarsall.bat
- ~\.nuget\packages\microsoft.dotnet.ilcompiler\10.0.3\build\Microsoft.NETCore.Native.Windows.targets
- ~\.nuget\packages\microsoft.netcore.app.runtime.nativeaot.win-x64\10.0.3\runtimes\win-x64\native\

Targets 细节：

**用 NativeLibrary Item 作为输入**  
支持类似：

```xml
<ItemGroup>
  <NativeLibrary Include="path\foo.lib" />
  <NativeLibrary Include="path\bar.lib">
    <WholeArchive>true</WholeArchive>
  </NativeLibrary>
</ItemGroup>
```

**覆盖两个 Host 流程**

- `BeforeTargets="_CreateAppHost;_CreateAppHostForPublish"`：重链 apphost，回写 `AppHostSourcePath`
- `BeforeTargets="_CreateSingleFileHost"`：重链 singlefilehost，回写 `SingleFileHostSourcePath`

**支持增量缓存**  
基于输入指纹（NativeLibrary 列表 + 包内 host libs + def + RID + machine + flags）生成 stamp，通过 Inputs/Outputs 控制跳过重复链接。

**使用 MSVC 工具链初始化**  
`build/findvcvarsall.bat`（可借鉴 ILCompiler）负责定位并初始化环境，再执行 `link.exe`。

**链接 Host**

- `*_obj.lib` 和 `*_directives.lib` 需要通过 `/wholearchive:libname` 链接。
- `singlefilehost` 需要 `/def:singlefilehost.def`，以确保正确 ord。
- ilc targets 中的 `SdkNativeLibrary` 在本项目中由 `directives.lib` 取代。

**目标**

- `_ResolveHostLinkInputs`
- `_GenerateAppHostFingerprint`
- `LinkAppHost`
- `_GenerateSingleFileHostFingerprint`
- `LinkSingleFileHost`

**错误信息**

- RID 不支持
- MSVC 环境初始化失败

**验证矩阵**

- `dotnet build`（无 `-r`）首次/二次（缓存）
- `dotnet build -r win-x64` 首次/二次
- `dotnet publish -r win-x64 /p:PublishSingleFile=true` 首次/二次

## 待办与已知问题

1. 当前构建出的 apphost 受程序间优化影响，难以跨项目复用，`StaticAppHost` 与“可复用静态 Host 模板”的预期仍有差距。
2. `ANGLE` 与 `SKIA` 同时静态链接时会引入两份 `zlib`，目前存在符号/目标冲突。
3. 当前实现按 `net10.0` 消费场景设计；其他目标框架（如 `net8.0`、`net9.0`）的行为尚未测试，也未定义兼容承诺。
4. 需要补齐面向消费端的链接扩展面（如 `LinkerArg`）与文档示例，明确“可配置能力”与“默认行为”的边界。
5. 需要补齐 `Avalonia` 与 `StaticAppHost` 的回归矩阵（build/publish、single-file、缓存命中、样例工程），并形成可复现的 CI 基线。
6. 需要明确跨 RID 计划与包结构策略（`win-arm64` 优先，后续 linux/loongarch），并约束每阶段的验收标准。
