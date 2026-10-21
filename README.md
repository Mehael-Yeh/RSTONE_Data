# RSTONE_Data

独立的 RSTONE 公开数据发布系统。Python 3.12 获取两个私有 Git 源的指定 commit，
生成完整签名清单、不可变 SHA-256 对象，并通过 GitHub Pages 分发。
数据更新不依赖 Flutter 构建；本项目不构建 APK，也不改动 RSTONE_Flutter。

**公开包里的全部原始字节均可被任何人读取、保存和传播。** 白名单是产品、应用、配方 Markdown 和 TDS，
包含源文件中已有的工程师、配方及供应商字段。正式发布需要审核范围、配置可信公钥和受保护的 Actions 私钥。
私有源保持私有，代码中没有生产私钥；未配置签名或公开范围批准时发布失败。

## 先运行本地验收

在 Python 3.12 虚拟环境中：

```text
python -m pip install --require-hashes -r requirements-ci.txt
python -m pytest -q
python scripts/generate_examples.py --check
python scripts/generate_casefold.py --check
python scripts/demo.py --output work/demo-new --report reports/demo.json
```

演示仅使用公开模拟数据和进程内临时私钥，启动的 localhost 服务在运行结束后关闭。
验证首次发布、文件变更、内容复用、跳过多个版本、递增序号回滚和无变化不发布。
输出目录需不存在，避免覆盖已有演示数据。模拟 commit 不是真实源证据。

`examples/flutter_protocol/` 是可独立运行的 Dart 协议示例，无 Flutter 工程和 APK：

```text
cd examples/flutter_protocol
dart pub get --enforce-lockfile
dart analyze
dart test
```

## 交付入口

| 内容 | 文件 |
| --- | --- |
| v1 冻结协议、签名字节、路径、同步和镜像语义 | [docs/DATA_PROTOCOL.md](docs/DATA_PROTOCOL.md) |
| 实际源结构和 187 文件审计 | [docs/SOURCE_AUDIT.md](docs/SOURCE_AUDIT.md)、[验证摘要](docs/SOURCE_VALIDATION.json) |
| 创建远程仓库、Secrets、Pages 和镜像 | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| 发布、异常删除批准、中断恢复、回滚与 GC | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Flutter 对接与原 APK 工作流迁移清单 | [docs/FLUTTER_INTEGRATION.md](docs/FLUTTER_INTEGRATION.md) |
| 逐条验收测试和证据边界 | [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) |
| 真实可验证的模拟分发站点 | [examples/distribution/](examples/distribution/) |
| 数据采集、发布、同步、离线导入命令 | `python scripts/data.py --help` |

## 发布结构

发布代码在默认分支；生成资源和恢复记录在独立 `distribution` 分支。
该分支的 **public/** 是静态站点根目录，包含 latest.json、manifests/、objects/、packages/；
state/ 不上传为 Pages 资源。只新增缺失 CAS 对象，完整清单引用所有有效路径，删除和移动由完整状态差异表达。
Git 按新增对象增量推送；Pages 自定义部署仍需要整站 artifact，客户端日常只下载缺少的对象。
`newObjects`/`uploadedBytes` 统计新增 CAS 对象和原始载荷，不冒充两次 Pages 站点 artifact 的实际传输流量。

工作流支持手动、repository_dispatch 和每六小时兜底检查。对象/清单部署并从公网逐一验证后，
才发布 latest；pending 状态直到最终公开部署验证完成才清除。镜像复制同一签名与对象，不自行签发版本。
历史默认保留 30 版、至少 7 天宽限，通过手动 Prune retained data history 工作流应用配置。

当前交付完成本地实现、实际资料格式验证及模拟端到端验收。
远程 RSTONE_Data、生产 Secrets、Pages 公网 URL、上游通知模板安装和 Flutter 客户端集成尚需按手册配置/协调，
本地测试不代表已经上线。
