# Phase 1：源结构审计（2026-10-21）

仅只读获取源快照，没有修改上游、Flutter 或任何远程仓库。实际源内容不进入此代码仓库。

| 数据源 | 分支 | 审计 commit | 白名单 | 文件数 |
| --- | --- | --- | --- | ---: |
| Mehael-Yeh/Rstone | master | 2d53b591ea884f115fb2e9c6c6644507e18d4325 | 产品列表/**/*.md | 99 |
| 同上 | master | 同上 | 产品应用/**/*.md | 21 |
| 同上 | master | 同上 | 产品配方/**/*.md | 16 |
| Mehael-Yeh/RstoneTDS | main | 28ee340aa72139eb2edbbdb218d050b6ceb6ebc2 | 根目录 *.TDS.md | 51 |

Flutter 审计 HEAD：06567fbe724a22fffaa6db3c10ffbbc1d9cc8a37。
`lib/models/product_item.dart` 读取 tags 数组、工程师、实验牌号、数值参数、底漆/中漆/面漆。
`lib/services/obsidian_data_service.dart` 用 `.TDS.md` 后缀提取名称，提供 RD/RS 归一化查找。
产品新增接口只要求 RD/RS 前缀，不能额外强加纯数字后缀。
配方和 TDS 允许无 Frontmatter；产品和应用要求开头的 YAML mapping。
引用包括 `[[产品]]`、配方引用，以及工程师等非产品 wiki 链接；只对产品引用报告失效警告。

现有 `release_production.yml`、`release_alpha.yml` 复制源 Markdown 后构建 APK，均为手动工作流。
`deploy_web_pages.yml` 也复制数据后构建 Flutter Web。迁移清单单独交付，不修改这些文件。

**公开边界**：产品字段包含工程师姓名；配方包含原料和供应商。原始字节发布意味着这些字段同样公开。
目录白名单不是字段脱敏。运维必须审核范围并配置 `PUBLIC_DATA_APPROVED=true` 后才可正式发布。
需要隐藏的资料应先移出发布白名单或另行批准转换方案，不能静默删字段、改写源资料。
`.obsidian/`、`.github/`、版本信息、附件、其他目录和非白名单文件一律不发布。

远程 `Mehael-Yeh/RSTONE_Data` 在审计时返回 404，尚无可验证的正式公开 URL、Pages 部署或生产密钥。
