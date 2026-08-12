## 背景

`StaticAppHost` 最初被设计成一个独立的 NuGet 包，host 静态输入文件按 RID 组织。

开发过程中逐渐暴露出几个问题：
1. `.NET Host` 启用了全程序优化，工具链版本被锁得很死；不修补上游代码，很难稳定关闭这类优化；
2. 静态输入文件体积很大，不适合继续沿着“可发布包”方向打磨；
3. 包消费体验仍需要大量时间完善，短期内很难达到理想状态。

因此，这次修改的核心判断是：`StaticAppHost` 不再以“可发布 NuGet 包”为目标，而是转为仓库内的源码级 MSBuild 集成，把注意力重新放回“如何稳定地重链接 Host”本身。

## 这次修改做了什么

### 1. hostlibs 产物从若干 `.lib` 改成 `.rsp`

在 Linux 上编译 `.NET Host` 时发现，Clang 没有与 MSVC 指令数据段完全等价的机制，而且链接参数顺序对结果有直接影响，因此预打包中间产物的形式也随之调整。

此前 `StaticAppHost` 依赖这些预打包中间产物：

- `libapphost_obj.lib`
- `libapphost_lib.lib`
- `libapphost_directives.lib`
- `libsinglefilehost_obj.lib`
- `libsinglefilehost_lib.lib`
- `libsinglefilehost_directives.lib`

现在，这组“二次打包的归档库”被 `AssetsDir` 下的响应文件替代：

- `apphost.rsp`
- `singlefilehost.rsp`

该归档逻辑现由 `native/hostlibs/conanfile.py` 维护：它直接从 runtime 的 ninja/link 规则中提取链接输入，复制真实依赖文件，并生成：

- `*.rsp`
- `*.linkflags`
- `singlefilehost.def`（Windows 下）

这样做有两个直接收益：一是 host 静态输入更贴近 runtime 的原始构建结果，减少了人为再封装一层 lib 带来的偏差；二是脚本也因此具备了在 Linux 上生成这套预打包中间产物的能力。

### 2. 去掉 `package-static-apphost` 包工程

原先位于 `src/package-static-apphost` 下的内容已经被拆除；对应地，MSBuild targets 现在直接放在仓库根 `src` 目录下，以源码文件的方式被消费。

`samples/simple-pinvoke` 也不再通过 `PackageReference` 消费 StaticAppHost，而是直接 import targets。这样一来，样例工程的职责就从“验证包消费”转为“验证 targets 本身”。

### 3. 把 Windows / MSVC 相关逻辑单独分层

`StaticAppHost.targets` 现在只保留跨平台、通用的链接流程框架，例如：

- 解析 `StaticHostRid`
- 计算输出路径与 fingerprint 路径
- 生成 apphost / singlefilehost 的输入指纹
- 在 `_CreateAppHost`、`_CreateAppHostForPublish`、`_CreateSingleFileHost` 之前触发重链接

`StaticAppHost.Windows.targets` 则承接所有 Windows 专属细节：

- 默认 `CppLinker`
- 调用 `findvcvarsall.bat` 初始化 MSVC 工具链
- RID 到机器架构 / `vcvars` 架构的映射
- Windows 链接参数与 `link.exe` 调用参数拼装

这样拆分之后，主 targets 文件更像“宿主重链接框架”，而 Windows 文件更像“一个 OS 后端实现”。这不仅让结构更清晰，也为后续补上 Linux 平台实现预留了接口。
