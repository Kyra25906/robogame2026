# 第二位算法同学 Git 提交说明

> 你的开发分支固定为 `feature/localization`。主要提交定位代码、定位测试、导航验收工具、定位参数和测试记录。不要直接向 `main` 写代码，也不要在未沟通时修改视觉、任务状态机、串口和运动控制核心文件。

仓库地址：

```text
https://github.com/Kyra25906/robogame2026.git
```

## 一、第一次准备

### 1. 接受仓库邀请

先登录自己的 GitHub 账号，接受 `Kyra25906/robogame2026` 私有仓库的协作者邀请。没有接受邀请时无法克隆私有仓库。

### 2. 在 Ubuntu 配置 Git 身份

下面的姓名和邮箱换成你自己的：

```bash
git config --global user.name "你的GitHub用户名"
git config --global user.email "你的GitHub邮箱"
git config --global core.quotepath false
```

最后一条让 Git 正常显示中文文件名。

查看是否正确：

```bash
git config --global user.name
git config --global user.email
```

### 3. 配置 GitHub SSH 密钥

```bash
ssh-keygen -t ed25519 -C "你的GitHub邮箱"
```

保存位置直接回车。然后显示公钥：

```bash
cat ~/.ssh/id_ed25519.pub
```

复制完整一行，进入 GitHub：

```text
头像 → Settings → SSH and GPG keys → New SSH key
```

只复制以 `.pub` 结尾的公钥内容。`~/.ssh/id_ed25519` 是私钥，不能发给任何人。

测试连接：

```bash
ssh -T git@github.com
```

第一次询问是否信任时输入 `yes`。

## 二、第一次下载代码

为了不覆盖已有文件，先克隆到新目录：

```bash
cd ~
git clone git@github.com:Kyra25906/robogame2026.git robogame_git
cd ~/robogame_git
```

检查：

```bash
git status
git remote -v
git branch --show-current
```

应当看到：

```text
当前分支：main
远程仓库：git@github.com:Kyra25906/robogame2026.git
工作区没有未提交修改
```

## 三、创建自己的定位分支

只需第一次执行：

```bash
git switch -c feature/localization
git push -u origin feature/localization
```

确认：

```bash
git branch --show-current
```

必须显示：

```text
feature/localization
```

如果显示 `main`，先不要写代码，切回自己的分支：

```bash
git switch feature/localization
```

## 四、开始编程前先做什么

### 1. 同步最新 main

```bash
cd ~/robogame_git
git status
git switch main
git pull --ff-only origin main
git switch feature/localization
git rebase main
```

如果第一条 `git status` 显示有未提交修改，不要直接切换分支或 rebase。先决定这些修改是否属于一个完整任务，并按后文方法提交。

### 2. 编译和跑基线测试

```bash
cd ~/robogame_git/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash

cd ~/robogame_git
python3 -m unittest discover -s tests -v
```

开始修改前应确保原有测试全部通过。如果基线已经失败，先把错误发出来，不要带着未知错误继续开发。

## 五、你允许提交哪些文件

你主要可以提交：

```text
ros2_ws/src/localization/localization/node.py
ros2_ws/src/localization/localization/quality.py
ros2_ws/src/localization/README.md
tests/test_localization.py
tests/test_navigation.py
tools/navigation_acceptance.py
ros2_ws/src/robogame_bringup/config/robot.yaml
docs/MCU_PROTOCOL.md
定位和导航测试记录
```

其中以下文件是计划新建的：

```text
localization/localization/quality.py
tests/test_localization.py
tools/navigation_acceptance.py
```

默认不要修改：

```text
cube_perception
motion_control/motion_control/node.py
robogame_core/navigation.py
robot_bridge/node.py
mission_manager
manipulator_client
robogame_interfaces
```

如果这些文件确实有问题，先提交复现步骤或发消息讨论，不要直接跨模块修改。

## 六、完成一小项以后怎么提交

### 1. 查看修改

```bash
cd ~/robogame_git
git status
git diff
```

逐项确认：

- 修改是否只与当前任务有关。
- 有没有调试时随手改的参数。
- 有没有密码、密钥、个人路径或无关文件。
- 有没有误加入 `build/install/log`。

### 2. 运行测试

先运行自己负责的测试：

```bash
python3 -m unittest tests.test_localization -v
python3 -m unittest tests.test_navigation -v
```

再运行全部测试：

```bash
python3 -m unittest discover -s tests -v
```

只有全部测试通过，才进入提交步骤。若某项必须依赖真车暂时无法测试，要在提交和 Pull Request 中明确写“未做真机验证”，不能写成已完成。

### 3. 只添加本次文件

例如第一次实现定位质量函数：

```bash
git add ros2_ws/src/localization/localization/quality.py
git add tests/test_localization.py
```

查看真正准备提交的内容：

```bash
git diff --cached
git status
```

不要养成每次都执行 `git add .` 的习惯，否则容易把无关修改混进来。

### 4. 创建提交

```bash
git commit -m "feat(localization): add sensor validity helpers"
```

然后推送：

```bash
git push
```

第一次推送该分支如果提示没有 upstream：

```bash
git push -u origin feature/localization
```

## 七、推荐的5个提交

不要把所有工作压成一个巨大提交，建议按下面拆分。

### 提交1：纯函数和测试

涉及：

```text
localization/quality.py
tests/test_localization.py
```

命令：

```bash
git add ros2_ws/src/localization/localization/quality.py
git add tests/test_localization.py
git commit -m "feat(localization): add sensor validity helpers"
git push
```

### 提交2：节点接入IMU过期和异常判断

涉及：

```text
localization/node.py
tests/test_localization.py
```

命令：

```bash
git add ros2_ws/src/localization/localization/node.py
git add tests/test_localization.py
git commit -m "fix(localization): fall back when IMU data is stale"
git push
```

### 提交3：配置和说明

涉及：

```text
robogame_bringup/config/robot.yaml
localization/README.md
```

命令：

```bash
git add ros2_ws/src/robogame_bringup/config/robot.yaml
git add ros2_ws/src/localization/README.md
git commit -m "docs(localization): document IMU timeout behavior"
git push
```

### 提交4：导航验收脚本

涉及：

```text
tools/navigation_acceptance.py
```

命令：

```bash
git add tools/navigation_acceptance.py
git commit -m "feat(tools): add navigation acceptance recorder"
git push
```

### 提交5：真车标定

只有拿到真实数据后再提交。提交内容应同时包含参数、原始测试记录和修改前后误差对比：

```bash
git commit -m "calib(localization): apply measured odometry scales"
```

不要在没有实测数据时创建这个提交。

## 八、提交信息怎么写

格式：

```text
类型(模块): 简短说明
```

常用类型：

- `feat`：新增功能。
- `fix`：修复错误或安全问题。
- `test`：只增加测试。
- `docs`：只修改说明。
- `calib`：根据实测数据修改参数。
- `refactor`：行为不变的代码整理。

好的示例：

```text
feat(localization): add sensor validity helpers
fix(localization): reject non-finite odometry
test(localization): cover stale IMU fallback
docs(localization): explain frame assumptions
calib(localization): update measured yaw scale
```

不好的示例：

```text
修改代码
更新
应该能用了
最终版本
test
```

## 九、如何发 Pull Request

完成一个可验收阶段后，在 GitHub 仓库页面选择：

```text
Compare & pull request
```

确认方向是：

```text
base: main
compare: feature/localization
```

Pull Request 标题示例：

```text
定位：增加IMU过期回退和输入有效性检查
```

说明按下面模板填写：

```text
## 修改内容
- 新增了什么
- 修复了什么

## 修改原因
- 原来会发生什么错误
- 为什么需要现在修改

## 测试
- 运行了哪些命令
- 通过多少项测试
- 模拟测试次数和结果

## 尚未验证
- 是否需要真车
- 哪些参数仍是暂定值

## 涉及文件
- 列出主要文件
```

提交 Pull Request 后不要立即自己合并，等待另一位同学检查。发现问题时继续向 `feature/localization` 推送修复，Pull Request 会自动更新。

## 十、Pull Request合并以后

同步最新主分支：

```bash
git switch main
git pull --ff-only origin main
git switch feature/localization
git rebase main
```

然后再开始下一项任务。

## 十一、遇到冲突怎么办

执行 `git rebase main` 时如果出现 `CONFLICT`：

```bash
git status
```

把完整输出发给另一位同学，确认双方是否修改了同一逻辑。解决冲突后：

```bash
git add 冲突文件路径
git rebase --continue
```

如果决定取消本次 rebase：

```bash
git rebase --abort
```

不要使用：

```text
git reset --hard
git push --force
删除整个项目重新下载
```

除非双方明确理解影响并共同决定。

## 十二、每天结束前检查

```bash
git branch --show-current
git status
git log --oneline -5
git push
```

向组内同步：

```text
当前分支：
最新提交编号：
今天完成的功能：
测试命令和通过数量：
仍需真机确认的内容：
Pull Request链接：
```

只有已经 `commit` 并 `push` 到 GitHub 的代码，才算完成同步。只存在于个人电脑中的文件不能作为团队交付版本。
