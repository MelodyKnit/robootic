# 提交前检查脚本

`check_submission_paths.py` 用于阻止文件系统绝对路径进入 Git 提交候选集。它通过 Git 列出已跟踪文件和未被忽略的新文件，因此不会扫描 `logs/`、`localstore/` 或其他被忽略的本地内容。

从项目根目录运行：

```powershell
python scripts/check_submission_paths.py
```

检查器会识别 Windows 盘符路径、UNC 路径、`file:` 后接两个斜杠的 URI 和常见 POSIX 绝对路径。网络 URL、仿真对象路径和相对路径不属于文件系统绝对路径，不会触发检查。

## 快捷批处理脚本 (Windows Batch Scripts)

`scripts/` 目录下提供 Windows 环境下的项目级批处理入口。所有脚本都必须从 `gripper-ai-controller` 项目根目录执行，不会根据脚本位置切换目录，也不会自动选择 `localstore/` 本机配置。脚本只接受 Poetry `1.8.5`；检测到 Poetry 2.x 或其他版本会以中文错误信息停止，避免改写当前 `poetry.lock`。

### 1. 依赖安装脚本 (`install.bat`)
```cmd
:: 从项目根目录安装项目依赖 (poetry install)
.\scripts\install.bat

:: 支持传入 Poetry 选项
.\scripts\install.bat --no-root
```

### 2. 运行脚本 (`run.bat`)
```cmd
:: 1. 网页服务必须传入明确配置文件，脚本不会使用默认或本机回退配置
.\scripts\run.bat web --config-file configs/development.json

:: 2. 仅当显式配置为 development 且绑定 127.0.0.1 时，才可使用 CLI 的 --reload
.\scripts\run.bat web --config-file configs/development.json --reload

:: 3. 其他 CLI 子命令可直接透传：
.\scripts\run.bat gpu-check --require-torch
```

### 3. 测试脚本 (`test.bat`)
```cmd
:: 默认运行全套单元与集成测试
.\scripts\test.bat

:: 运行指定模块或测试方法
.\scripts\test.bat tests.test_cli
```
