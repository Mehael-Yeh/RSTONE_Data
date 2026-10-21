# 实现验收与证据边界

验收环境：Windows、Python 3.12.14、Dart 3.13.6 stable，2026-10-21。
GitHub Actions 的 Ubuntu/Windows 矩阵已配置；远程运行结果见公开仓库 Actions，以下本地记录不代替远程运行结果。
预装 Dart 开发版分析器存在环境错误，已使用 SHA-256 校验过的官方独立稳定版复核，未更新现有 Flutter SDK。

## 可重现的测试

```text
python -m pip install --require-hashes -r requirements-ci.txt
python -m pytest -q --junitxml=reports/pytest.xml
python scripts/generate_examples.py --check
python scripts/generate_casefold.py --check
python scripts/demo.py --output work/demo-new --report reports/demo.json
```

Dart：在 examples/flutter_protocol 执行 `dart pub get --enforce-lockfile`、`dart analyze`、`dart test`。
Python lint 与 Actions 校验分别使用 Ruff 0.17.1 和 actionlint 1.7.12；actionlint 二进制来自官方发布并核对 SHA-256。
依赖 hash lock 安装已运行。测试仅在临时 Git 仓库、临时签名密钥和本地 HTTP 服务运行，不改动源仓库或构建 APK。
最终验收：65 项 Python 测试、13 项 Dart 测试；Dart analyze、Ruff、3 个 Actions 工作流的 actionlint 均通过。

| 用户验收项 | 实际运行测试 |
| --- | --- |
| 1 首次完整发布 | test_release::test_first_publication |
| 2 新增 Markdown | test_release::test_add_modify_delete_and_full_latest |
| 3 修改 Markdown | 同上，验证新对象数与完整清单 |
| 4 删除 Markdown | 同上，验证清单删除和旧对象仍保留 |
| 5 重命名 | test_release::test_rename_move_reuses_objects |
| 6 不同目录移动 | 同上，以及 test_client::test_skip_versions_only_download_changed_objects_and_keep_overlay |
| 7 跨版本复用对象 | test_release::test_same_content_multiple_paths_and_noop_new_commit |
| 8 重复源内容 | 同上，源 commit 改变但内容相同也不发布 |
| 9 获取失败 | test_build::test_fetch_failure_has_no_fallback、test_source_failure_and_empty_directory |
| 10 防止误空库 | test_release::test_bulk_deletion_and_empty_are_never_silently_published |
| 11 签名校验 | test_release::test_scope_key_and_signature_fail_closed；Dart 互通/伪签名测试 |
| 12 异常批量删除 | 上述批删测试、test_intentional_namespace_empty_requires_exact_approval |
| 13 发布中断恢复 | test_interruption_and_recovery（5 个断点）、test_staging_verification_pointer_last_and_final_deploy_recovery |
| 14 历史恢复 | test_rollback_new_sequence_history_gc_and_zip |
| 15 跳版本完整同步 | test_client::test_skip_versions_only_download_changed_objects_and_keep_overlay，删除中间 manifests 后仍可同步 |
| 16 部分镜像缺对象 | test_mirrors_supply_same_target_objects_even_with_stale_latest、test_all_mirrors_missing_object_retains_previous_generation |

额外覆盖：Git 原始 CRLF/BOM、恶意路径/重复 JSON/YAML/别名、凭证扫描、错误源 origin、无生产密钥失败、
签名与 latest 字段绑定、错误对象不推进、Git state 冷启动/恢复、CORS、同序号冲突、最高序号防回放、
客户端中断/本地损坏修复、用户 overlay 保护、带签名 ZIP 和恶意 ZIP 条目、GC current/pending 引用保护。

## 真实源证据

两个私有源均通过当前登录账号只读获取，精确 commit 和统计见 SOURCE_AUDIT.md、SOURCE_VALIDATION.json。
187 份资料实际通过编码/YAML/命名校验；99 个工程师字段需公开范围审核；无失效产品引用警告。
这证明指定 Git 快照的资料格式兼容，不等于真实资料已公开，也不等于上线后不会出现其他业务错误。

## 模拟端到端结果

scripts/demo.py 使用模拟源和真实 Ed25519/HTTP 流程：首次客户端下载 5 个对象（409 字节）；
从版本 1 直接同步版本 4 只下载 1 个对象（172 字节）；回滚成新 sequence 5，无需再次下载对象；
同样内容再次运行为 unchanged，保留 1..5 历史序号。报告明确 officialPublication=false、apkBuilt=false。
模拟例子的 sourceCommits 为占位 SHA，不可冒充上游提交；模拟私钥只存在于该进程内。

## 阶段提交

| 阶段 | 提交/校验 |
| --- | --- |
| Phase 1 审计与协议 | d96b17f；12 tests passed |
| Phase 2 采集、对象清单和差异 | 9b14d96；23 tests passed，实际 187 文件审计通过 |
| Phase 3 签名、恢复、回滚、GC | d1722c3；38 tests passed |
| Phase 4 CI/Pages/镜像 | e7ebf02；45 tests passed，工作流 actionlint 通过 |
| Phase 5 完整客户端参考、互通和文档 | 当前交付提交；65 Python + 13 Dart tests passed，详见交付说明和 reports/pytest.xml |

## 公开仓库与尚待外部验收

公开代码仓库已创建并推送：[Mehael-Yeh/RSTONE_Data](https://github.com/Mehael-Yeh/RSTONE_Data)，默认分支 `main`。
真实私有源内容未随代码推送，生产发布没有启用。

- 配置 Pages、受保护发布环境、私有源只读 token、签名私钥和可信公钥。
- 确认全部公开字段范围，设定真实首个兼容客户端 minAppVersion，再启用 PUBLIC_DATA_APPROVED。
- 在两个源仓库协调安装通知模板，验证实际源 commit 更新触发数据发布且没有 APK 构建。
- 在真实公开 HTTPS URL 检查完整对象、签名、CORS、两阶段切换和客户端真实联网/离线行为。
- 在 Flutter 仓库协调集成同步/存储/界面，并完成 Android/iOS/Web 设备验收。

上述外部步骤没有在本地执行，也没有伪造在线发布成功。Pages 整站 artifact 传输与客户端文件增量是不同指标，详见 OPERATIONS.md。
