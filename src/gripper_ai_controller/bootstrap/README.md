# 运行时组装

`bootstrap` 是组件组装代码，而不是配置文件目录。它读取调用方从项目根目录 `configs/` 指定的 JSON 文件，完成校验、解析显式注册的组件工厂，并构建一个新的 `RuntimeConfig`。

`runtime_builder.py` 负责完整运行时图的配置加载。`preview_builder.py` 复用同一 JSON 校验工具，但只构造一个 `VisionAdapter` 与经校验的网页预览设置；它不会创建 `Runtime`、执行目标、插件或安全策略。由于 Python 3.7 标准库提供 `json`，当前仅支持 JSON；YAML 需要额外依赖，不能被隐式接受。

构建器绝不根据源码文件路径推导项目根目录。命令行通过 `--config-file` 传入路径，确保部署路径明确。开发模式重载组件时，`build_runtime()` 会重新读取同一个文件；网页预览不支持或调用运行时热重载。
