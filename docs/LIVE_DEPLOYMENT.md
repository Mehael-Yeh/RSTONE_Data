# 生产配置与公网验收记录

配置日期：2026-10-21。代码仓库为公开的 [Mehael-Yeh/RSTONE_Data](https://github.com/Mehael-Yeh/RSTONE_Data)。
两个源仓库保持私有，数据发布范围仍仅限 SOURCE_AUDIT.md 中的四类原始 Markdown。

## 配置

- Pages 使用 GitHub Actions，HTTPS 已开启，baseUrl 为 `https://mehael-yeh.github.io/RSTONE_Data/`。
- `github-pages` environment 只允许 `main`；不要求每次人工批准，以支持自动发布。
- `data-operations` 只允许 main，回滚、异常删除批准和历史清理需 Mehael-Yeh 独立审核；该 gate 无 Secrets。
  通知令牌仅 Actions:write，不能批准需要 Deployments:write 的审核接口。
- `DATA_SIGNING_KEY_ID=production-2026`，私钥只存于 environment Secret，公钥见 `config/trusted_keys.json`。
- `PUBLIC_DATA_APPROVED=true` 对应本次已授权的公开范围，包含工程师、配方、供应商等源字段。
- Rstone/master 与 RstoneTDS/main 各有一枚独立的只读 deploy key；发布器不拥有源库写入凭证。
- 两个源库各有默认分支限定的 `rstone-data-notify` environment；通知使用仅 Actions:write 的受限令牌。
  用户已在两边保存 DATA_DISPATCH_TOKEN，两条通知链路均已验证成功，运行证据见下文。
- 数据仓库四组 active rulesets 要求 main 通过 PR、仅所有者合并，禁止 distribution 和标签的强制更新/删除。
  所有者的 main 例外仅用于 PR 合并。已经撤销测试用通知 write key 和信号分支，不复用个人广泛权限 token。
- Actions 默认权限为 read，不能批准 PR；发布任务才声明所需写权限。
  源通知任务仅使用通知令牌，不接触生产签名私钥或私有源读取密钥。
- 保留 30 个历史版本，GC 宽限 7 天；源内容大幅减少需精确批准，整体空库始终禁止发布。
- `minAppVersion=1.0.0` 为保留的首个 v1 客户端版本下限；现有 Flutter 0.0.263 尚未接入此协议。

## 验收

首次真实发布完成：[运行 39544209701，attempt 2](https://github.com/Mehael-Yeh/RSTONE_Data/actions/runs/39544209701)。
首轮因状态分支保护拒绝 GitHub 内置 token 的写入，在公开 latest 创建之前停止；修正为允许正常
fast-forward、禁止 force push/删除后重跑成功。没有发布空库，没有降低签名校验要求。

- `sequence=1`，`dataVersion=2026.10.21.001`，发布时间 `2026-10-21T04:33:35Z`。
- 共 187 文件：产品列表 99、产品应用 21、产品配方 16、TDS 51。
- 新对象 187，新增原始对象载荷 84,388 字节；此数字不含 Pages 两次整站 artifact 的实际传输。
- Manifest SHA-256：`e6e32540db6ecbda3cb869343b22bb4a400d28a73331ed858e59506228ef882f`。
- 发布流程完成公网全部对象/签名/CORS 校验，最终 latest 验证成功后清除 pending。
- 独立从本机分别完整验证 Pages 和 raw 备用入口：均为 sequence=1、187 文件、签名/大小/SHA-256/CORS 通过。
- 真实联网 Python 参考客户端：首次对象 GET 187 次 / 84,388 字节，再次同步对象 GET **0 次 / 0 字节**。
- [内容未变的再次正式运行](https://github.com/Mehael-Yeh/RSTONE_Data/actions/runs/39545095058) 成功：
  187 份均未变化，0 新对象、0 新增载荷，没有签发 sequence=2，也没有再次部署 Pages。
- Pages 的 `/state/pending.json` 与 `/sources/Rstone/.git/config` 返回 404；只有 public/ 被上传为站点。

公网入口：[Pages latest](https://mehael-yeh.github.io/RSTONE_Data/latest.json)、
[raw latest](https://raw.githubusercontent.com/Mehael-Yeh/RSTONE_Data/distribution/public/latest.json)。
二者共用 GitHub 基础设施，不能视为独立服务商容灾。观测到 Pages Cache-Control 为 max-age=600，raw 为
max-age=300，CORS 均为 `*`；客户端为 latest/清单加随机查询参数并验证签名，不能假设已配置秒级 CDN 刷新。
完整机器可读证据及签名 manifest 的两个准确源 SHA 见 [DEPLOYMENT_VERIFICATION.json](DEPLOYMENT_VERIFICATION.json)。

2026-10-21，用户完成两处通知 Secret 配置后，实际验收两条链路：

| 源分支 | 源通知运行 | 被触发的数据发布 | 结果 |
| --- | --- | --- | --- |
| Rstone/master | [39550160974](https://github.com/Mehael-Yeh/Rstone/actions/runs/39550160974) | [39550172045](https://github.com/Mehael-Yeh/RSTONE_Data/actions/runs/39550172045) | 两端成功，unchanged，sequence=1 |
| RstoneTDS/main | [39550303922](https://github.com/Mehael-Yeh/RstoneTDS/actions/runs/39550303922) | [39550315143](https://github.com/Mehael-Yeh/RSTONE_Data/actions/runs/39550315143) | 两端成功，unchanged，sequence=1 |

两次发布均为 187 份未变、0 新对象、0 新增载荷，Pages 上传/部署步骤均跳过；
普通通知无需 data-operations 审批，公开 latest/manifest 与验收前完全相同。
此次以 workflow_dispatch 启动源库现有通知工作流，没有修改业务资料；源库 push 仍按白名单路径和默认分支过滤。
数据发布端的外部配置已完成。令牌到期后按 DEPLOYMENT.md 轮换，手动和六小时定期发布继续作为备用。
没有把旧的广泛权限登录 token 复制到 Actions，验证过程未读取 Secret 的值。

本项目未修改 Flutter 客户端和 APK 构建流程，也没有构建 APK。上述真实客户端证据来自 Python 参考实现，
Flutter 设备、业务界面和官方/用户数据存储层集成仍是独立客户端工作。
