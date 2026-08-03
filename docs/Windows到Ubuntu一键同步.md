# Windows 到 Ubuntu 一键同步

本项目推荐使用“SSH 密钥 + 一键同步脚本”，而不是 VMware 拖拽或共享文件夹。

脚本只同步以下内容：

- `ros2_ws/src`
- `tests`
- `docs`
- `README.md`

不会传输 ROS2 的 `build`、`install`、`log`，也不会传输临时分析文件。

## 第一次配置

在 Windows PowerShell 中进入项目：

```powershell
cd "C:\Users\dahli\Documents\Codex\2026-07-13\xu"
powershell -ExecutionPolicy Bypass -File .\tools\setup_ubuntu_key.ps1
```

第一次连接时输入 `yes`。输入 Ubuntu 登录密码时，终端不会显示字符，这是正常的。

创建密钥时如果询问 passphrase，直接按两次回车。配置成功后会显示：

```text
SSH key login OK
Setup complete.
```

## 日常同步

双击：

```text
tools\sync_to_ubuntu.cmd
```

或者在 Windows PowerShell 中运行：

```powershell
.\tools\sync_to_ubuntu.cmd
```

脚本会自动：打包源码、上传、覆盖 Ubuntu 对应源码、ROS2 编译、运行自动测试。

## 常用选项

只同步，不编译也不测试：

```powershell
.\tools\sync_to_ubuntu.cmd -SkipBuild -SkipTests
```

同步并编译，但不运行测试：

```powershell
.\tools\sync_to_ubuntu.cmd -SkipTests
```

虚拟机 IP 改变后：

```powershell
.\tools\sync_to_ubuntu.cmd -UbuntuHost 192.168.253.129
```

先在 Ubuntu 用 `hostname -I` 确认新 IP。

## 重要说明

- Windows 目录是源码主版本，Ubuntu 是运行和硬件测试环境。
- 脚本会覆盖同名文件，但不会删除 Ubuntu 中已经多出来的旧文件。
- 现场在 Ubuntu 做出的重要修改应及时复制回 Windows 或提交到 Git，避免下一次同步覆盖。
- 如果上传成功但编译失败，文件通常已经同步；根据窗口最后的编译错误处理即可。
