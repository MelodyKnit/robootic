# 提交前检查脚本

`check_submission_paths.py` 用于阻止文件系统绝对路径进入 Git 提交候选集。它通过 Git 列出已跟踪文件和未被忽略的新文件，因此不会扫描 `logs/`、`localstore/` 或其他被忽略的本地内容。

从项目根目录运行：

```powershell
python scripts/check_submission_paths.py
```

检查器会识别 Windows 盘符路径、UNC 路径、`file:` 后接两个斜杠的 URI 和常见 POSIX 绝对路径。网络 URL、仿真对象路径和相对路径不属于文件系统绝对路径，不会触发检查。

## 快捷批处理脚本 (Windows Batch Scripts)

`scripts/` 目录下提供 Windows 环境下的项目级批处理入口。`install.bat` 与 `test.bat` 必须从 `gripper-ai-controller` 项目根目录执行；`run.bat` 会切换到自身所属的项目根目录。所有入口都使用项目既有 Poetry 环境，不会自动选择 `localstore/` 本机配置。

项目执行入口支持 Poetry `>=1.7,<3`，已覆盖 Poetry 1.x 的兼容锁格式和 Poetry 2.x 的已有环境启动方式。`check_poetry.bat` 是运行和测试入口共用的版本检查器；低于 1.7 或未来未验证的 3.x 会被明确拒绝。首次安装和构建必须使用 Poetry 1.7/1.8，维护依赖锁必须使用 `lock.bat` 和 Poetry 1.8.5，以避免生成旧版无法读取的新锁格式。

Poetry CLI 应独立运行在其支持的宿主 Python 中，不应安装进项目的 Python 3.7 环境。项目虚拟环境仍必须指向 `robotic` 的 Python 3.7；`pyproject.toml` 固定使用 `poetry-core==1.6.1`，以保证 Python 3.7 下的 PEP 517 构建可用。当前 Poetry 2.3 的解释器探测实现不能首次创建或构建 Python 3.7 环境，因此 2.x 只用于已经建立环境中的 `run.bat`、`test.bat` 和元数据检查。

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

### 4. 锁文件维护脚本 (`lock.bat`)

```cmd
:: 仅重新计算项目元数据哈希，不更新已解析的软件包版本
.\scripts\lock.bat
```

该入口只接受 Poetry 1.8.5。Poetry 2.x 可以正常执行安装、测试和启动，但不得用于重写 `poetry.lock`。
