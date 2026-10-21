# 发布、回滚和故障恢复手册

## 日常发布

先按 DEPLOYMENT.md 完成外部配置。源分支固定：Rstone/master、RstoneTDS/main。
手动运行 Publish RSTONE Data，或由源更新通知/每六小时扫描触发。
不要把源通知接到 Flutter APK 工作流。代码或公钥变更走审核；不能用任意 PR 代码取得发布 Secrets。

普通发布保持自动。传入 rollback_sequence、deletion_approval 或运行历史清理时，必须由所有者
在 Actions 的 Review deployments 中批准 `data-operations`，之后才会启动有签名/发布权限的任务。
通知令牌只授予 Actions:write，不能批准需要 Deployments:write 的审核接口；不要给该令牌额外部署权限。
本地 CLI 仍需拥有私钥和精确删除批准，适用于受信操作员，不接受来自通知任务的生产私钥。

报告计数：added、modified、deleted、unchanged 是逻辑路径对账；newObjects 是新增的唯一 CAS 对象数，
uploadedBytes 是这些对象的原始字节之和，sourceCommits 是本次实际读取的 Git HEAD。
Git 分发分支只推送新 blob。GitHub Pages 的两次完整站点 artifact 会重传已有资源，
此托管传输量不计入新增对象指标；它不影响客户端下载增量。Pages 不提供文件级上传 API。
若连托管层也必须只传缺失对象，需使用支持逐对象 PUT 的镜像，`scripts/mirror.py` 已提供挂载站点目录版本。

报告在 Actions artifact `data-publication-report`；正式版本审计在 distribution/state/reports。
pending 恢复摘要保留该候选首次准备时的计数并标记 resumed=true，不表示恢复运行重新写入了这些对象。
源 checkout 或 Secrets 缺失会记录 failure.json 和 Actions 日志，不回退到空数据。
源版本完全相同不签发新序号；commit 不同但有效 Markdown 原始字节相同，也不发新版。

只读本地审计，不需要生产签名密钥：

```text
python scripts/data.py fetch --directory work/sources-new --report reports/fetch.json
python scripts/data.py audit --rstone work/sources-new/Rstone --tds work/sources-new/RstoneTDS --report reports/audit.json
```

fetch 使用当前 Git 凭证助手（本地可先 `gh auth setup-git`），目标目录必须新建，任一获取失败即停止。
CI 用 checkout 的两个专用只读 deploy key。采集读取 Git blob，忽略未提交工作区修改，并验证源 origin 身份。
不把 .obsidian、版本信息、环境配置、附件或未批准目录当作分发资源。

## 正常批量删除

默认按总库和各命名空间检测超过 25% 的文件数量减少/不可复用删除内容。
相同哈希的移动不会单独触发内容丢失检查；删除副本导致的数量崩塌仍被检测。
超过阈值会失败，错误报告提供 `baselineSequence:mappingSha256`。

审核 changes.deleted、四类文件数和两组 commit，确认是有意操作；在手动工作流 deletion_approval 原样填入该 token。
批准只适用于该基线和目标完整映射；源内容或正式基线变化后重新审核。没有全局 `--force` 或永久关闭保护开关。
可以清空某一类资料，但 Rstone 预期目录须保留受跟踪 `.gitkeep`，并提供精确批准。
整个数据库为空、源获取失败、源目录不存在、无效签名/路径/凭证内容均不能批准绕过。
引用失效是警告，应用方案不会被自动删除。

## 中断与恢复

重新运行同一工作流：存在 pending 时优先继续该候选，不取新的源状态覆盖它。
这包括第一次部署失败、公网验证失败、已更新本地 latest 但第二次部署失败、最终验证后 checkpoint 失败。
旧 latest 或已验证的新 latest 都能继续服务；恢复后下一次扫描再处理后续源变化。

本地硬中断留下 `.release.lock` 时，先确认没有发布/同步/镜像进程还在运行，再手动删除**对应 store 根目录**的该锁文件。
不自动按超时抢锁，不删对象“重来”。签名或对象损坏时停止发布，保留故障目录和日志；从分发 Git 历史恢复缺失字节并验证。
中断留下的孤立不可变清单不覆盖，后续序号可跳号；GC 遇到不完整、无法验签的资源停止并要求检查，不把它视为可安全删除的数据。

如果 CDN 暂时 404 或返回旧 latest，自动有限重试；多次重试失败后保留 pending，下一次运行恢复。
不要手工把 latest 指向一个未逐项验证的版本。不要取消正在写入的发布以启动另一份并行写入。

## 正式回滚

在 Publish RSTONE Data 的 rollback_sequence 填入保留的历史序号。
无需读取上游；先校验目标历史清单/签名/对象，再用生产密钥签发新的递增序号。
文件映射、sourceCommits 和 minAppVersion 来自历史目标，rollbackOf 指明目标。
历史清单原字节不变，已缓存对象不重传。历史已被清理时先从 Git 历史恢复到隔离目录并验证，不能直接降低 latest.sequence。

若发布密钥疑似泄露，先暂停外部发布入口/撤销 Secret，准备客户端信任锚更新，再按协议轮换。
`setup_signing.py` 不覆盖已有 keyId 或 Secret；轮换先用 `--secret-name DATA_SIGNING_PRIVATE_KEY_2027` 安装独立新 Secret，
保留现有 Secret 服务当前发布。发布含新公钥的客户端、保留历史公钥后，再切换 DATA_SIGNING_KEY_ID 和 DATA_SIGNING_SECRET_NAME。
两项选择不匹配时会失败，不能签出错误版本；切换期间避免启动发布。旧私钥可在轮换确认完成后按管理员策略撤销。
不要把 `examples/trusted_keys.json` 的示例 keyId 放到生产可信表。

## 历史清理

调整 config/release.json 的 retainVersions（默认 30）和 gcGraceDays（默认 7）后审核提交。
运行 Prune retained data history 工作流；它与发布串行，存在 pending 时拒绝维护，全部被保留版本校验后才清理。
latest 和保留/pending 版本仍引用的对象绝不删除；在宽限期内的版本和新对象也保留。
Git 历史依然保存已公开数据，清理站点不能撤回公开资料。

先在分发状态的独立副本检查计划：

```text
python scripts/data.py gc --store work/distribution --retain 30 --grace-days 7 --report reports/gc-plan.json
python scripts/data.py gc --store work/distribution --retain 30 --grace-days 7 --apply --report reports/gc-applied.json
```

第二条会修改本地副本；正常在线维护使用工作流，以便同步 Git 和 Pages。若 pending 遭遇站点容量不足，
可在隔离副本中按计划保护 current/pending 清理更老资源，审核后 checkpoint 并重跑发布；不得删 pending 所需对象。
站点逼近 1 GB 时应提前配置对象镜像/容量方案，不能靠反复重试解决容量不足。

## ZIP 与参考客户端下载

```text
python scripts/data.py package --store work/distribution --sequence 1
python scripts/data.py import --package examples/distribution/packages/000002.zip --cache work/offline-client --keys examples/trusted_keys.json
python scripts/data.py sync --base-url https://primary.example/data --base-url https://mirror.example/data --cache work/client --keys config/trusted_keys.json --app-version 1.0.0
```

ZIP 导入先验签，再逐条核对原始内容，拒绝额外路径/重复项/软链接。每日同步用 sync，不下载 ZIP。
参考缓存的 active.json 原子保存 generation 和最高已接受序号。official 文件在 generations/*/files，用户资料放独立 overlay。
示例保留旧 generations，生产 Flutter 应在确认没有 UI 读者后再进行本地磁盘回收；磁盘不足时保持旧 generation，不提前删除。
