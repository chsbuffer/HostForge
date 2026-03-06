# NuGet 打包与测试流程

## 一键流水线

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\pipeline.ps1
```

## 目录约定

- NuGet 包项目：`src/package-static-apphost`
- 测试消费项目：`samples/simple-pinvoke`

## 测试前准备

1. 在仓库根目录打包静态 AppHost 包：

```powershell
dotnet pack src\package-static-apphost\StaticAppHost.csproj -c Release -v:minimal
```

2. `SimplePInvoke` 已通过本地包源还原（`samples/simple-pinvoke/SimplePInvoke.csproj`）：

- `$(MSBuildProjectDirectory)\..\..\src\package-static-apphost\bin\Release`

## 验证矩阵（在仓库根目录执行）

1. 打包 NuGet：

```powershell
dotnet pack src\package-static-apphost\StaticAppHost.csproj -c Release -v:minimal
```

2. `dotnet build`（无 `-r`）首次：

```powershell
dotnet build samples\simple-pinvoke\SimplePInvoke.csproj -c Release -v:minimal
```

3. `dotnet build`（无 `-r`）二次（验证缓存命中）：

```powershell
dotnet build samples\simple-pinvoke\SimplePInvoke.csproj -c Release -v:minimal
```

4. `dotnet build -r win-x64` 首次：

```powershell
dotnet build samples\simple-pinvoke\SimplePInvoke.csproj -c Release -r win-x64 -v:minimal
```

5. `dotnet build -r win-x64` 二次（验证缓存命中）：

```powershell
dotnet build samples\simple-pinvoke\SimplePInvoke.csproj -c Release -r win-x64 -v:minimal
```

6. `dotnet publish -r win-x64 /p:PublishSingleFile=true` 首次：

```powershell
dotnet publish samples\simple-pinvoke\SimplePInvoke.csproj -c Release -r win-x64 /p:PublishSingleFile=true -v:minimal
```

7. `dotnet publish -r win-x64 /p:PublishSingleFile=true` 二次（验证缓存命中）：

```powershell
dotnet publish samples\simple-pinvoke\SimplePInvoke.csproj -c Release -r win-x64 /p:PublishSingleFile=true -v:minimal
```

8. 运行程序：

```powershell
.\samples\simple-pinvoke\bin\Release\net10.0\win-x64\publish\SimplePInvoke.exe
```

## 成功判定

- 首次构建/发布日志出现：
  - `Microsoft (R) Incremental Linker`
- 二次相同命令不再触发 link（命中缓存）
- 可执行文件正常运行
