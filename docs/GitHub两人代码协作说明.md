# GitHub 两人代码协作说明

## 1. 推荐结构

GitHub 私有仓库是源码中心：

```text
main                         只放通过测试、可以演示的稳定版本
feature/vision-integration   视觉、抓放、状态机和集成分支
feature/localization         第二位同学的定位和标定分支
```

每个人只在自己的功能分支开发，不直接向 `main` 随意推送。合并前运行测试，由另一人检查改动。

## 2. 第一次把 Windows 项目上传到 GitHub

### 2.1 在 GitHub 网页创建仓库

登录 GitHub，创建一个私有仓库，例如 `robogame2026`：

- Visibility 选择 `Private`。
- 不勾选自动创建 README。
- 不勾选 `.gitignore`。
- 不勾选 License。

创建后复制仓库 HTTPS 地址，例如：

```text
https://github.com/你的用户名/robogame2026.git
```

### 2.2 在 Windows 初始化并提交

```powershell
cd "C:\Users\dahli\Documents\Codex\2026-07-13\xu"

git init -b main
git config user.name "你的姓名或GitHub名称"
git config user.email "你的GitHub邮箱"

git status
git add .
git status
git commit -m "chore: initialize RoboGame ROS2 workspace"
```

第二次 `git status` 时要确认没有以下目录：

- `ros2_ws/build`
- `ros2_ws/install`
- `ros2_ws/log`
- `work`
- `outputs`
- 大型视频和压缩包

连接远程仓库并上传：

```powershell
git remote add origin https://github.com/你的用户名/robogame2026.git
git push -u origin main
```

GitHub 不接受账号密码作为 Git 操作密码。Windows 通常会由 Git Credential Manager 打开浏览器，让你登录并授权。

## 3. 邀请另一位同学

GitHub 仓库页面进入：

```text
Settings → Collaborators → Add people
```

输入对方的 GitHub 用户名。对方接受邀请后才能访问私有仓库。

不要共享自己的 GitHub 密码或访问令牌。

## 4. 双方在 Ubuntu 配置 GitHub SSH 密钥

每个人在自己的 Ubuntu 执行：

```bash
ssh-keygen -t ed25519 -C "自己的GitHub邮箱"
```

保存位置直接回车使用默认值；是否设置密钥口令由本人决定。然后显示公钥：

```bash
cat ~/.ssh/id_ed25519.pub
```

复制完整一行，进入 GitHub：

```text
头像 → Settings → SSH and GPG keys → New SSH key
```

粘贴并保存。测试：

```bash
ssh -T git@github.com
```

第一次询问主机可信时输入 `yes`。成功时会显示已经通过身份验证。私钥 `~/.ssh/id_ed25519` 不能发给队友，也不能上传仓库；只复制以 `.pub` 结尾的公钥。

## 5. 双方第一次在 Ubuntu 下载

为了不覆盖旧项目，先克隆到新目录：

```bash
cd ~
git clone git@github.com:你的用户名/robogame2026.git robogame_git
cd ~/robogame_git
```

编译和验证：

```bash
cd ~/robogame_git/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash

cd ~/robogame_git
python3 -m unittest discover -s tests -v
```

确认新目录编译、测试都成功后，再决定是否把旧的 `~/robogame` 归档。不要在第一次克隆前直接删除旧目录。

## 6. 创建各自分支

视觉与集成负责人：

```bash
cd ~/robogame_git
git switch -c feature/vision-integration
git push -u origin feature/vision-integration
```

定位负责人：

```bash
cd ~/robogame_git
git switch -c feature/localization
git push -u origin feature/localization
```

分支只需要第一次创建。之后用 `git branch --show-current` 检查自己当前在哪个分支。

## 7. 每天开始工作

先保存本地未提交修改，然后同步最新主分支：

```bash
cd ~/robogame_git
git status
git switch main
git pull --ff-only origin main
git switch feature/localization
git rebase main
```

视觉负责人把最后一个分支名换为 `feature/vision-integration`。

如果 `git status` 显示有未提交修改，不要直接切换或 rebase；先完成一次有意义的提交。

## 8. 完成一小项后的提交方法

先看修改：

```bash
git status
git diff
```

只添加本任务涉及的文件，不要习惯性把所有文件一起提交。例如定位同学：

```bash
git add ros2_ws/src/localization
git add tests/test_navigation.py
git diff --cached
git commit -m "feat(localization): reject stale IMU data"
git push
```

提交信息示例：

```text
feat(perception): add ROI contour filtering
fix(motion): stop when pose becomes stale
test(localization): cover stale IMU fallback
docs: update MCU interface checklist
```

一个提交只解决一个主要问题，便于另一人检查和出错后撤回。

## 9. 合并到 main

在 GitHub 网页对自己的功能分支创建 Pull Request：

```text
feature/localization → main
```

说明中填写：

- 改了什么。
- 为什么要改。
- 如何测试。
- 测试次数和结果。
- 哪些内容仍需真机确认。

另一人检查后再合并。合并完成后，双方执行：

```bash
git switch main
git pull --ff-only origin main
```

## 10. 冲突时怎么处理

看到 `CONFLICT` 时不要继续盲目运行 `git add .`。先执行：

```bash
git status
```

联系同时修改该文件的人，逐段决定保留哪边。解决后运行相关测试，再执行：

```bash
git add 冲突文件路径
git rebase --continue
```

如果不确定如何解决，保留终端输出和 `git status`，不要使用 `git reset --hard`。

## 11. GitHub 与一键 SSH 同步脚本的关系

- GitHub：两个人交换源码、审查、保留历史，是正式协作方式。
- `tools/sync_to_ubuntu.cmd`：把当前 Windows 工作区快速部署到自己的 Ubuntu，适合个人调试。
- `build/install/log`：每台 Ubuntu 自己生成，绝不上传 GitHub。
- 视频和 rosbag：体积大，不放普通 Git；保存在共享云盘，并在测试记录中写链接和文件名。

团队交付版本以 GitHub `main` 的提交编号为准，不以某个人电脑里“最新的文件夹”为准。
