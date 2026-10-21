# 生产配置与公网验收记录

配置日期：2026-10-21。代码仓库为公开的 [Mehael-Yeh/RSTONE_Data](https://github.com/Mehael-Yeh/RSTONE_Data)。
两个源仓库保持私有，数据发布范围仍仅限 SOURCE_AUDIT.md 中的四类原始 Markdown。

## 配置

- Pages 使用 GitHub Actions，HTTPS 已开启，baseUrl 为 `https://mehael-yeh.github.io/RSTONE_Data/`。
- `github-pages` environment 只允许 `main`；不要求每次人工批准，以支持自动发布。
- `DATA_SIGNING_KEY_ID=production-2026`，私钥只存于 environment Secret，公钥见 `config/trusted_keys.json`。
- `PUBLIC_DATA_APPROVED=true` 对应本次已授权的公开范围，包含工程师、配方、供应商等源字段。
- Rstone/master 与 RstoneTDS/main 各有一枚独立的只读 deploy key；发布器不拥有源库写入凭证。
- 两个源库各有默认分支限定的 `rstone-data-notify` environment；通知使用仅 Actions:write 的受限令牌。
  凭证需用户本人创建并存入 DATA_DISPATCH_TOKEN；是否已验证见下文，缺失时不冒充即时通知正常。
- 数据仓库四组 active rulesets 要求 main 通过 PR、仅所有者合并，禁止 distribution 和标签的强制更新/删除。
  所有者的 main 例外仅用于 PR 合并。已经撤销测试用通知 write key 和信号分支，不复用个人广泛权限 token。
- Actions 默认权限为 read，不能批准 PR；发布任务才声明所需写权限。通知接收器没有权限或生产 Secrets。
- 保留 30 个历史版本，GC 宽限 7 天；源内容大幅减少需精确批准，整体空库始终禁止发布。
- `minAppVersion=1.0.0` 为保留的首个 v1 客户端版本下限；现有 Flutter 0.0.263 尚未接入此协议。

## 验收

首次真实发布和上游通知链正在执行验证。只有本页记录成功的运行和公开 HTTPS 校验结果后，才声明上线完成。
本项目不修改 Flutter 客户端和 APK 构建流程。
