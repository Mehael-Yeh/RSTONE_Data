# RSTONE Data Protocol v1

状态：v1 冻结。协议版本 `1`，业务 schema `1`。破坏性变更必须新协议版本。
本文是 Flutter 和所有镜像的共同开发依据。数据是公开、匿名可下载的完整原始 Markdown，任何人均可读取、保存、传播。

## 1. URL 和编码

同一 baseUrl 下：`latest.json`、`manifests/000001.json`、`manifests/000001.sig`、
`objects/<64位小写SHA256>`、可选 `packages/000001.zip`。
sequence 超过六位不截断。所有资源用相对路径；客户端不能接受索引内任意 URL。
生产仅 HTTPS，不携带登录凭证。JSON UTF-8，无 BOM；发布器使用键排序、紧凑分隔符、
非 ASCII 原字符、单个末尾 LF；没有浮点数。签名验证直接使用下载到的原始字节，禁止 JSON 重编码后验签。

## 2. latest.json

```json
{"protocolVersion":1,"sequence":1,"dataVersion":"2026.10.21.001","manifestPath":"manifests/000001.json","manifestSha256":"<64位小写SHA256>","signaturePath":"manifests/000001.sig","keyId":"production-2026"}
```

sequence 为 1..9007199254740991 严格递增整数，是唯一排序依据。dataVersion 为显示字段，不能排序或作为文件定位依据。
latest 是可变发现指针，不单独签名。manifest 中也签入 keyId、sequence、dataVersion；
客户端必须核对 latest 的这些字段和 protocolVersion 全部等于已验签的 manifest，并核对 manifestSha256。
禁止把未签名 latest 的序号写入本地最高序号。历史 replay 通过本地持久化已接受最高序号防止；
首次安装无已知序号时不能识别服务端持续提供的旧签名快照（freeze attack），不得宣称解决该问题。

## 3. 完整 Manifest

必须包含 protocolVersion、sequence、dataVersion、schemaVersion、minAppVersion（纯数字 SemVer）、
publishedAt（UTC `YYYY-MM-DDTHH:mm:ssZ`）、keyId、sourceCommits、files。
sourceCommits 精确包含 `Mehael-Yeh/Rstone` 和 `Mehael-Yeh/RstoneTDS` 两个键，对应 40 位小写 Git SHA。
每个文件记录 path、sha256、size 和可选 contentType（默认 `text/markdown; charset=utf-8`）。
完整可运行示例在 `examples/distribution/`，包含真实可验证的模拟对象和签名，不包含私有源内容。
可直接读取 [完整 Manifest](../examples/distribution/manifests/000002.json)、
[latest](../examples/distribution/latest.json) 和 [示例公钥](../examples/trusted_keys.json)。

files 是当前全部有效文件，按 path 的 UTF-8 字节升序排列，不是补丁。空清单非法。
大小是原始字节数（1..16 MiB），对象哈希基于原始字节，保留源 LF/CRLF、BOM、正文空白。
有效 UTF-8 BOM 可存在于 Markdown，解析时忽略但不改变哈希。
同哈希的所有记录大小相同。最多 100000 个文件，JSON/客户端另有总下载大小上限。
JSON 重复键、大小写折叠后冲突路径必须拒绝。

path 使用 `/`，UTF-8 且已为 Unicode NFC，不主动改写名称。根目录只能为 产品列表、产品应用、产品配方、产品TDS。
禁止绝对路径、反斜线、空组件、`.`、`..`、隐藏组件、控制字符、Windows 保留名、尾点/尾空格、`<>:"|?*`。
路径最多 1024 UTF-8 字节、组件最多 240 字节；后缀精确为 `.md`，TDS 精确为 `.TDS.md`。
子目录保持原组织；当前 RstoneTDS 只发布根目录 TDS。路径不能直接拼接成不受检查的本地文件路径。

## 4. 对象和状态语义

`objects/<sha256>` 保存文件原始字节，不按原路径覆盖。相同内容跨路径、跨版本复用同一个对象。
旧路径缺失=删除；相同路径不同哈希=修改；新路径=新增；重命名/移动=删除+新增，相同哈希不传输。
客户端直接比较本地已接受完整清单与目标完整清单，可跨过任意多个版本，无须逐版应用事件。
服务器扫描到内容完全相同不发布新版本，即使源 commit 改变；摘要仍报告本次观察到的 commit。

## 5. Ed25519 签名和密钥

签名对象是 manifest JSON 文件**全部原始字节**（包括最后 LF）；纯 Ed25519，不先做 SHA256 再签名，不使用 Ed25519ph。
独立 `.sig`：RFC4648 标准 Base64、带 `=` padding、无折行，最后单个 ASCII LF；解码恰为 64 字节。
公钥：32 字节 Ed25519 raw public key 的标准 Base64；keyId 来自应用预装的可信键表。
仓库公钥表配置为 `{"keys":{"production-2026":"<Base64 raw public key>"}}`；这是构建/运维输入，不是网络信任根更新机制。
生产私钥：32 字节 seed 的 Base64，只在受保护 Actions environment Secret 中保存，初始名称 `DATA_SIGNING_PRIVATE_KEY`。
轮换可用独立的新 Secret，由受保护的 DATA_SIGNING_SECRET_NAME 选择；脚本环境变量始终为 DATA_SIGNING_PRIVATE_KEY。
不得提交私钥，不得把私钥放入 Flutter，不得把网络下载的公钥直接当信任锚。
发布器必须证明私钥与受信 keyId 公钥匹配。缺失、错误密钥均失败；本地测试临时生成独立密钥，无不签名的正式发布模式。

轮换：先发布包含新公钥和新 keyId 的客户端，保留旧公钥用于历史校验，再切换发布私钥；
旧客户端不认识新 keyId 应停止更新并提示升级，保持旧数据。撤销/信任锚变更通过可信客户端更新，
不凭未签名的 latest 或下载的 keys.json 自动信任新钥。历史 manifest 不重新签名。

## 6. 客户端事务与镜像

1. 获取各镜像 latest（短缓存、可加 nonce），按序号选择兼容且验签成功的候选。
2. 下载原始 manifest 和签名，核对 SHA256、签名、绑定字段、schema/minAppVersion、路径唯一性和大小上限。
3. 对目标每个哈希检查本地缓存原始字节。缺少/损坏的对象从任一镜像的同一哈希路径获取，核对大小和哈希。
4. 不同镜像可提供同一目标对象；禁止改用该镜像的另一份 manifest 或拼接文件映射。
5. 在独立新 generation 中构造完整官方文件树，全部验证成功后原子替换 active 指针，并持久化完整已签名清单及最高序号。
6. 中断、404、超时、哈希不匹配、未知公钥均保留旧 active。旧 generation 清理应延迟，不能影响正在读取的 UI。

同序号不同 manifest hash 必须报冲突，不接受。小于已接受最高序号拒绝；同序号同 hash 是幂等恢复。
官方删除只影响官方 generation，用户新增/自定义资料放独立 overlay，不随官方清单删除。
缓存落后的镜像可继续提供旧对象；目标任一对象全部镜像均不可用时不切换。
镜像验收必须验证目标所有对象，不能只探测 latest 或 HEAD。

## 7. 发布事务、恢复和回滚

发布器串行执行；对象原子写入并校验→不可变 manifest/签名→验证所有引用→最后原子替换 latest。
静态 Pages 是整站部署，没有逐文件上传事务。实现分两次部署：第一阶段部署对象/新清单且保留旧 latest，
经公开 URL 对本次所有对象及清单验签通过后，第二阶段才部署新 latest。
CDN 各边缘节点不能全局原子切换；客户端事务和跨镜像重试提供最终一致性，不得宣称 CDN 全局强一致。
候选和上次正式状态持久化到专用 distribution 分支，失败重跑先恢复 pending，禁止覆盖 pending 的不可变清单。
新的 sequence 大于所有已保留/预留的 sequence，中断可留下序号空洞。

发布器另输出结构化运行摘要（非签名的运维报告，不供客户端做信任判断）：status、sequence、dataVersion、
added/modified/deleted/unchanged 路径数量、newObjects、uploadedBytes、sourceCommits、mappingSha256、warnings 和 changes 路径数组。
newObjects/ uploadedBytes 指新增唯一 CAS 对象及其原始载荷；不包括 Pages 整站 artifact、Git 压缩封装或 HTTP 开销。
无变化为 unchanged，不产生新 manifest；失败为 failed 且提供不包含凭证内容的 error。

回滚是新发布：文件映射及 sourceCommits 使用选定历史已验签状态，使用新的 sequence/dataVersion/publishedAt/keyId，
记录 rollbackOf 历史序号（可选扩展）。不降低序号、不覆盖旧清单。回滚允许切回旧 minAppVersion/schema。
正常发布的异常删除需精确匹配当前基线 sequence 和目标文件映射 SHA256 的批准，避免批准失效后误用。
完整空库及缺失任一预期源目录不允许批准绕过。首次发布要求四类资料均非空；
之后有意清空某类资料可用精确批准，Rstone 的预期目录需保留受跟踪占位文件（如 `.gitkeep`）。
获取失败与成功读取到零文件是不同状态，前者始终失败。移出目录白名单等价于从公开库删除，不扩大公开范围。

## 8. 保留、ZIP、缓存

历史保留数可配置，默认 30；GC 仅显式运行，保护当前、最新 N 个历史和 pending 全部引用。
先验证被保护清单/签名/对象再做标记清扫。删除不可变资源需 CDN 宽限期（默认至少 7 天）。
离线客户端用新完整清单和当前对象追赶，历史清理不会要求历史补丁存在。
Git 历史仍保存曾发布的内容；GC 不等于撤回公开数据。
ZIP 为辅助资源，包含 manifest、签名和按逻辑路径放置的原始文件；导入必须逐文件按签名 manifest 校验，
拒绝多余条目/重复条目/路径穿越，不能把 ZIP 完整性当作真实性。日常更新不用 ZIP。
ZIP 条目精确为 `metadata/manifest.json`、`metadata/manifest.sig` 和每个 `files/<逻辑path>`；
不包含 latest、不包含对象哈希目录、不添加目录条目。总解压上限 544 MiB，其中文件正文合计最多 512 MiB、
manifest 最多 32 MiB、签名最多 512 字节；不接受软链接或加密条目。导入仍检查 minAppVersion 和本地最高已接受序号。

对象/清单建议 `Cache-Control: public,max-age=31536000,immutable`，latest 建议 `max-age=60,must-revalidate`。
Pages 不能通过仓库文件任意设置 HTTP 响应头，不能依赖 `_headers` 在 Pages 生效；客户端重验证/nonce 和内容校验是必需的。
自有 CDN 镜像配置匿名 GET/HEAD/OPTIONS、`Access-Control-Allow-Origin: *`、无 Cookie、无 Authorization。
部署后实际探测 CORS 响应头；浏览器 fetch 跨源 smoke test 另见运维手册。

参考：[GitHub Pages 工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)、
[Pages 限制](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)、
[Ed25519 API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)。
