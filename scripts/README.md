# 提交前检查脚本

`check_submission_paths.py` 用于阻止文件系统绝对路径进入 Git 提交候选集。它通过 Git 列出已跟踪文件和未被忽略的新文件，因此不会扫描 `logs/`、`localstore/` 或其他被忽略的本地内容。

从项目根目录运行：

```powershell
python scripts/check_submission_paths.py
```

检查器会识别 Windows 盘符路径、UNC 路径、`file:` 后接两个斜杠的 URI 和常见 POSIX 绝对路径。网络 URL、仿真对象路径和相对路径不属于文件系统绝对路径，不会触发检查。
