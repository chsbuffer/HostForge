# HostForge Linux 交叉编译方案形成记录

## 结论

HostForge 当前的 Linux 构建采用交叉编译方案。

构建时使用现代宿主机上的 `clang`，目标环境使用与 `.NET` 官方 Linux host 基线一致的 `Ubuntu 18.04 bionic` sysroot。`.NET host`、`SkiaSharp` 和 `HarfBuzz` 使用同一套 ABI 基线，目标 `GLIBC` 版本为 `2.27`。

## 背景

HostForge 当前的 Linux 产物包含两部分原生代码来源：

- `.NET host` 侧的 `apphost` / `singlefilehost`
- `SkiaSharp` / `HarfBuzz` 侧的附加静态库

早期工作先在现代 Linux 发行版上完成了 `apphost` 和 `singlefilehost` 的本机编译。引入 `SkiaSharp 3.119` 后，工具链基线、链接方式和运行时兼容性问题开始集中暴露。

## 为什么这样做

### 1. 产物检查显示 ABI 基线需要统一

在 `Arch Linux + clang 21 + gcc 15` 环境下，`apphost` 和 `singlefilehost` 已经可以编译通过。继续处理 `SkiaSharp 3.119` 时，出现了高版本 `llvm` 与 `gcc` 相关问题，对应参考包括：

- `https://github.com/llvm/llvm-project/issues/95133`
- `https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator/issues/312`
- `https://gcc.gnu.org/gcc-13/porting_to.html#header-dep-changes`

随后在 `Ubuntu 22.04 + gcc 11.4.0` 环境中完成了一版 host 与 skia 的组合构建，并执行：

```bash
readelf -p .comment singlefilehost
```

结果如下：

```text
String dump of section '.comment':
  [     0]  Ubuntu clang version 14.0.0-1ubuntu1.1
  [    28]  Linker: LLD 21.1.8
  [    3b]  GCC: (GNU) 15.2.1 20260209
  [    56]  GCC: (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0
```

这说明产物中已经混入多个来源的工具链信息。接下来需要确认整条构建链路最终使用的是哪条 ABI 基线。

### 2. `.NET` 官方 host 产物给出了基线线索

继续检查原版 `.NET singlefilehost` 的 `.comment` 信息后，得到：

```text
String dump of section '.comment':
  [     0]  GCC: (Ubuntu 7.5.0-3ubuntu1~18.04) 7.5.0
  [    2a]  Linker: LLD 20.1.8
  [    3d]  clang version 20.1.8
```

这个结果体现出目标环境 GCC 注记与现代 `clang` / `lld` 注记同时存在。HostForge 因此采用“宿主工具链 + 目标 sysroot”的交叉编译路径，并以 `.NET` 官方 Linux host 的基线作为对齐目标。

### 3. `.NET runtime` 文档和脚本提供了直接方法依据

交叉编译方案的主要依据来自以下文件：

- `https://github.com/dotnet/runtime/blob/v10.0.3/docs/workflow/building/coreclr/cross-building.md`
- `https://github.com/dotnet/runtime/blob/v10.0.3/eng/common/cross/build-rootfs.sh`

其中：

- `cross-building.md` 给出了交叉编译入口：先生成 `ROOTFS`，再通过 `ROOTFS_DIR` 配合 `build.sh --cross` 进入交叉编译流程
- `build-rootfs.sh` 给出了 sysroot 的构造方式：基于 `debootstrap --variant=minbase` 初始化 rootfs，重写源配置，在 `chroot` 内安装开发包和运行时依赖，最后执行 `symlinks -cr /usr`

这些内容确定了 HostForge 的 sysroot 构造方法和交叉编译入口。

### 4. `SkiaSharp` 提供了参数传递方式

`SkiaSharp` 的 Linux 构建参考主要来自：

- `https://github.com/mono/SkiaSharp/blob/v3.119.2/native/linux-clang-cross/build.cake`

这里保留下来的方法依据主要是 cross clang 的参数组织方式，包括：

- 通过 `--target` 指定目标三元组
- 通过 `-B`、`-I`、`-L` 指向工具链根目录
- 在需要时显式传递 `--sysroot`

HostForge 在此基础上统一为：

- 使用宿主 `clang`
- 显式传递 `--sysroot`
- 统一 `CC=clang`
- 统一 `CXX=clang++`

此外，`libfontconfig1-dev` 被视为 Linux 侧构建 `SkiaSharp` 的前置依赖。

## 实际要求

### 1. sysroot 基线

最终选定的目标 sysroot 为：

- `Ubuntu 18.04 bionic`

这套基线用于对齐 `.NET` 当前 Linux host 的兼容范围，并将最终产物控制在 `GLIBC 2.27`。

### 2. sysroot 构造要求

构建过程依赖一个可用的 sysroot。用于交叉编译的 sysroot 和一般的 rootfs 的关键区别在于执行 `symlinks` 修复库路径。缺少这一步时，`clang` 在解析库时会沿绝对符号链接回到宿主机，实际问题表现为找不到 `libdl.so.6` 的符号。

### 3. 统一构建链路

最终落地后的 Linux 原生构建链路如下：

- `.NET host` 使用宿主 `clang + sysroot`
- `SkiaSharp` / `HarfBuzz` 使用宿主 `clang + 同一 sysroot`

这保证了最终链接结果使用同一套 ABI 基线。

## 验证方法

方案验证主要依赖以下命令：

```bash
readelf -p .comment singlefilehost
```

```bash
readelf -V singlefilehost | grep -oP 'GLIBC_\d\.\d+' | sort -V | tail -n1
```

验证目标包括：

- `.comment` 中同时出现宿主 `clang` 与目标环境 `gcc` 的信息
- 最高 `GLIBC` 版本保持在 `2.27`
- 构建过程和最终链接过程能够稳定通过

## 总结

HostForge 当前 Linux 交叉编译方案的核心内容如下：

1. 使用现代宿主机上的 `clang` 作为构建工具链。
2. 使用 `Ubuntu 18.04 bionic` sysroot 作为目标环境基线。
3. 依据 `.NET runtime` 的 `build-rootfs.sh` 构造 sysroot。
4. 依据 `SkiaSharp` 的 cross clang 参数方式，将 `SkiaSharp` / `HarfBuzz` 并入同一套构建链路。
5. 通过 `.comment` 和 `GLIBC_2.27` 检查确认产物 ABI 基线。

这就是 HostForge 当前 Linux 构建方法的形成结果。
