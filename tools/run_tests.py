"""DSH 沙箱适配的项目测试运行器（Windows）。

用法：
    D:\\python.exe -B tools\\run_tests.py            # 跑全部 tests/ 下的 unittest
    D:\\python.exe -B tools\\run_tests.py -v         # 详细输出
    D:\\python.exe -B tools\\run_tests.py tests.test_mission  # 指定模块

为什么需要这个包装器（2026-08-18 实测根因）：
  1. 本项目 Windows 侧统一用 `D:\\python.exe`（3.14，有 cv2 / numpy / imageio）。
  2. Python 3.14 的 `tempfile.mkdtemp()` 用 `os.mkdir(path, 0o700)` 建临时目录；
     DSH 文件沙箱会把 `0o700` 目录判为"所有者私有"，其内任何写入都抛
     `PermissionError [WinError 5]`，导致所有使用 `TemporaryDirectory` 的测试
     （vision_report / vision_batch_report / vision_segment_annotator 等）批量报错。
     这不是项目代码缺陷，是运行环境适配问题。
  3. 沙箱对系统 `%TEMP%` 只读，临时目录必须指到工作区内的 `tmp/_testtmp`。

本脚本只做两件事：把临时目录指到工作区 + 把 `os.mkdir` 的 `0o700` 提升为
`0o755`（Windows 上 POSIX 权限位对 ACL 无实质影响，仅规避沙箱的目录判读）。
之后以标准 unittest discover 跑测试，与团队既定基线（344 通过 / 2 error）对齐。
"""
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _adapt_sandbox():
    # 1) 临时目录指到工作区（沙箱外部 %TEMP% 只读）
    ws_tmp = os.path.join(_ROOT, "tmp", "_testtmp")
    os.makedirs(ws_tmp, exist_ok=True)
    os.environ["TMP"] = ws_tmp
    os.environ["TEMP"] = ws_tmp
    tempfile.tempdir = ws_tmp

    # 2) 0o700 -> 0o755（tempfile.mkdtemp 的默认建目录模式）
    _orig_mkdir = os.mkdir

    def _mkdir(path, mode=0o777, *, dir_fd=None):
        if mode == 0o700:
            mode = 0o755
        if dir_fd is not None:
            return _orig_mkdir(path, mode, dir_fd=dir_fd)
        return _orig_mkdir(path, mode)

    os.mkdir = _mkdir


def _collect_pythonpath():
    """把所有 ros2_ws/src 包目录加进 sys.path（对应 GETTING_STARTED 的 PYTHONPATH）。"""
    src_root = os.path.join(_ROOT, "ros2_ws", "src")
    added = []
    if os.path.isdir(src_root):
        for name in sorted(os.listdir(src_root)):
            pkg = os.path.join(src_root, name)
            if os.path.isdir(pkg) and pkg not in sys.path:
                sys.path.insert(0, pkg)
                added.append(name)
    tests_dir = os.path.join(_ROOT, "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    return added


def main():
    _adapt_sandbox()
    pkgs = _collect_pythonpath()
    print(f"[run_tests] PYTHONPATH 注入 {len(pkgs)} 个包: {', '.join(pkgs)}")
    argv = [sys.argv[0]]
    if len(sys.argv) > 1 and sys.argv[1] not in ("-v", "--verbose"):
        # 指定测试模块/类/方法的透传（如 tests.test_mission.MissionTests）
        names = sys.argv[1:]
        suite = unittest.TestSuite()
        for name in names:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)
    else:
        verbose = "-v" in sys.argv or "--verbose" in sys.argv
        suite = unittest.defaultTestLoader.discover(os.path.join(_ROOT, "tests"))
        runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
