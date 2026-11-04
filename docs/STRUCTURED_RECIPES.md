# 统一配方协议：Python 发布器

本实现消费父冻结的 `rstone-recipe/1` 十键模型和 `rstone-recipe-markdown/1` renderer。不是 Markdown 自动转换器，也不是发布批准。

## 正常发布

- `Store.prepare` 正常入口只产生 manifest `schemaVersion=2`，wire `protocolVersion=1` 不变。四个 `.md` 命名空间和 `contentType` 不变；最低 App 版本至少为 `2.0.2`。
- 每份配方必须完整通过 canonical codec：ASCII 单行 canonical JSON、精确十键、UTF-8/LF、标量/深度/长度限制，以及正文重新渲染的全字节相等。未知或损坏新格式不走 legacy fallback。
- owner 使用全量产品目录中的精确完整名称，最长注册名称优先；title 必须等于准确文件 stem。用途尾缀为封闭集合，不能把真实产品尾缀当用途删除。
- `associatedProducts` 全量核验，不做别名、大小写或后缀猜测。仅 main 可贡献身份信用，而且 owner 必须出现在真实正投量原料行中；供应商、备注、元数据中的 owner 不算信用。
- 所有 classification 的每一行投入数都必须可解释：十进制数值或低位不高于高位的区间，或封闭的未知量措辞（适量／少量／若干／另加／按当量另加／开稀至可喷粘度／按NCO/OH=…另加／适量（NCO/OH=…））。负数、倒置区间、任意文本在目录门即拒；仅 main 额外要求正投量 owner。App 目录门使用同一口径。
- 保留五列的每格原串、空尾格、范围、零量和未知另加；保留 preTable 和 content 中说明及所有工艺分支、步骤的次序。新版本不得给同路径重算 recipeId，也不得把已有 ID 转交另一 owner。
- typed→typed 命名资格比较全部五列、columns、preTable、有序 content、owner/classification 等事实。保留所选原 recipeId；同事实的合并来源必须保全所有注册关联及其相对次序。两列 BOM 相同不足以取得资格。

## 固定迁移见证与独立新许可

固定位置均相对于发布器自己的 `config/`；没有环境变量、source、Snapshot warnings 或 CLI 的策略覆盖入口。

- `recipe_migration_witnesses.json`：`{"schema":"rstone-recipe-migration-witnesses/1","witnesses":[]}`。
- registry 中的每个 SHA 对应 `config/migration_witnesses/<sha256>.json`。加载器校验完整文件 SHA、闭合字段、版本、重复键/记录、类型、大小及 symlink。
- 单份 proof 使用 `rstone-recipe-migration-witness/1`。root 精确字段：schema、protocol、renderer、sourceCatalogApprovalFingerprint、baselineManifestSha256、beforeInventorySha256、approvedLegacyInventorySha256、typedInventorySha256、modelFactsSha256、records。
- records 每项精确为 pathSha256、legacySha256、legacySize、modelSha256、typedSha256、typedSize。必须覆盖全部 typed 配方，无遗漏或重复。model SHA 是冻结 codec.header 的 ASCII 字节 SHA；facts 是按 pathSha256 排序的 path/model SHA 记录的 canonical JSON 摘要。inventory 是完整 `{path:{sha256,size}}` 的 UTF-8 canonical JSON（紧凑、排序、末尾 LF）。
- proof 同时绑定已验签基线、完整 before、原已批准 legacy inventory、typed after 及全部模型内容。旧 v1 approval 必须仍存在，但它只证明历史批准前提，绝不批准新字节。
- `catalog_revisions_v2.json` 默认为空：`{"schema":"rstone-catalog-revisions/2","approvals":[]}`。v2 事务 fingerprint 绑定完整 before/after 路径+SHA+size、准确基线 manifest SHA、协议/renderer、精确 scope、proof 完整文件 SHA。没有 proof、任一 byte 漂移、越 scope 或没有独立新 fingerprint 许可，都拒绝。
- 旧 `catalog_revisions.json` 保持字节不变。旧 apply 接口只能返回未豁免 findings，不能再授予新发布权；旧 Markdown helper 仅保留历史单元刻画。

只读提案命令：

```sh
PYTHONDONTWRITEBYTECODE=1 /absolute/venv/bin/python -B -m rstone_data.catalog_revisions_v2 \
  --before /private/before --after /private/after --baseline-manifest /private/baseline.json
```

它只输出 proposed-only / ineligible / unavailable，不写策略、不安装见证、不签名。CLI 所给基线只用于提案；实际发布始终重新验签 Store 的真实基线，提案输入不赋予权限。

## 历史 schema1 恢复

schema1 可验签读取，不可普通 prepare 新建或改写。恢复必须经 release 层验历史 manifest/签名、完整路径集合、每个 SHA/size 和原 sourceCommits；保留原 schemaVersion/minAppVersion。普通入口没有 legacy 或 schemaVersion 强制开关。

所有恢复、resume、promote、complete、package 都重新验证。pending 的 baseline 还须匹配真实当前指针或其已验签前驱；不能把 qualityBaselineSequence 改成 null、删除前驱 hash 来伪装成首次发布。schema2 恢复源还须重新验证原事务资格，不能利用被遗弃的签名 pending 绕过撤销的 proof/许可。恢复 marker 必须是严格小于当前 sequence 的整数，不能自引用或形成循环。schema1 重新发版也必须通过当前价格、秘密、身份等门禁；恢复不会自动批准删除。

删除阈值不超过原 `0.25`。所需删除批准独立、绑定准确映射；记录连同 manifest SHA 和基线 sequence 持久化，所有发布出口再次核验。CLI rollback 明确传递用户给出的独立删除 token。

镜像复制也是发布出口：首次目标写入之前预检整个待复制历史；每个 schema2 代都经过当前 release 门禁。旧 schema1 历史只按原 manifest/签名的完整对象集合逐字节复制，不生成新的 legacy manifest 或授予新的 legacy 内容权利。

## 内容检查与载体

源文件使用真实逻辑命名空间准入，产品/应用/TDS 不得伪装成 recipe envelope。无逻辑名的 CAS/ZIP 则独立解码内容，不伪造 TDS 路径来套用旧 scalar-only YAML 门。

两条路径均执行完整 codec/body 比对，并扫描解码后的业务字段、结构键、组装视图及原路径/raw bytes。组装视图只按文档顺序连接业务文本（title、preTable、五格、说明、工艺标签与步骤），不插入 schema 键或 kind 枚举；同一表述拆到相邻说明／步骤必须与写在单格等价被检查。价格、秘密、联系方式不会因 Unicode JSON 转义、数字实体或分片而豁免；原 YAML scalar 约束没有放宽。Python 参考客户端在完整 catalogue 到齐后验证 schema2 配方，再原子激活；坏 schema2 保留 last-good。

冻结 golden 的 shape 接受不等于发布接受：其中合法形状含合成 `$1` 字面量，仍须被价格门拒绝。公开 fixture/测试仅为合成数据；真实预览及既有签名数据不进入此补丁。
