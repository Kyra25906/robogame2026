# Windows 到 Ubuntu 一键同步

本项目推荐使用“SSH 密钥 + 一键同步脚本”，而不是 VMware 拖拽或共享文件夹。

## 推荐方式：Git 干净副本同步并自动验收

项目提交并推送 GitHub 后，在 Windows 项目根目录运行：

```powershell
.\tools\sync_accept_ubuntu.cmd
```

脚本会自动读取当前提交号，并依次执行：

```text
确认 Windows 已提交并推送
→ Ubuntu 从 GitHub 创建独立干净仓库
→ 检出 Windows 当前提交
→ colcon build
→ Python 单元测试
→ 机构模拟闭环 smoke
→ 单动作摘要验收
→ 保存 CSV 和 robot_bridge 日志
```

脚本不再依赖 `/home/panwenhui/robogame` 或 `/home/panwenhui/robogame2026`。两个旧目录都不会被覆盖；全新的验收仓库按提交号命名，例如：

```text
/home/panwenhui/robogame_acceptance_fc8150e
```

只检查本次将使用的提交和路径，不连接虚拟机：

```powershell
.\tools\sync_accept_ubuntu.cmd -PlanOnly
```

虚拟机 IP 改变时：

```powershell
.\tools\sync_accept_ubuntu.cmd -UbuntuHost 192.168.253.129
```

验收结果位于新副本的 `acceptance_results/`。只有脚本最后显示 `ACCEPTANCE PASS`，才表示构建、测试和模拟验收全部完成。

下面的压缩包覆盖方式保留作备用，适合无法从 Ubuntu 访问 GitHub 时使用，但它不会创建同等严格的干净提交副本。

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
