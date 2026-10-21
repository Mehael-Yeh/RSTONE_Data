# 交付给 RSTONE_Flutter 的对接说明

本项目没有修改 Flutter 仓库。先审核 DATA_PROTOCOL.md，再在独立客户端变更中集成。
`examples/flutter_protocol/` 是可运行 Dart 库和测试，验证 Python 原始 manifest 的 Ed25519 签名、路径、字段绑定、哈希、版本及增量计划。
`scripts/rstone_data/client.py` 提供完整可执行的多镜像下载、缓存、原子切换和离线 ZIP 导入参考。
Dart 示例不代替应用存储层、Web IndexedDB、网络重试 UI 或后台任务；这些需在客户端仓库实现并做实际设备验证。

生产入口已验证，可使用 `config/mirrors.json` 中的 Pages 与 GitHub raw 两个 baseUrl；二者属于同一服务商，
不等于独立基础设施容灾。首版为 sequence=1，dataVersion=2026.10.21.001，共 187 份文件。
客户端编译时固定 `config/trusted_keys.json` 的 production-2026 公钥；不能运行时下载公钥后自动信任。
Python 参考客户端已真实联网验证首次 187 个对象 / 84,388 字节，第二次同步 0 个对象请求；这不是 Flutter 设备验收。

## 接入当前服务

当前 `ObsidianDataService` 把 assets/产品列表.json、产品应用.json、产品配方.json、产品TDS.json 当索引，
随后逐文件 rootBundle.loadString。新客户端从已验证完整 manifest 构造相同分类列表，按相对路径加载官方 generation 的原始 Markdown。
路径含子目录时保留完整相对路径，不只保存 basename；不同目录同名文件不能在 Map 中静默覆盖。
TDS 仍以精确 `.TDS.md` 去后缀取得产品名，保留 RD/RS 别名查找。

建议新增 DataSyncService + OfficialDataStore，ObsidianDataService 依赖只读资料提供器。
移动端/桌面：应用私有目录 objects/、generations/、active.json；Web：同等语义的 IndexedDB 事务。
不要把先删旧目录再复制新目录当成事务。应用启动可先显示已验签的本地官方库，后台检查更新；失败继续离线使用。
官方和用户新增的 manual_items、手工产品/应用/配方、tag_alias_rules.custom.txt 分开保存。
应用官方删除只换官方文件映射，绝不按 manifest 对用户数据目录做全局删除。

## 同步状态机

1. 从配置的 HTTPS baseUrl 取 latest，用查询 nonce 避免长期缓存，不给匿名请求附加授权信息。
2. 下载 manifest 原始 bytes 和独立 Base64 .sig；用 app 内置 keyId→公钥表调用 Dart verifyManifest。
3. 同序号不同已接受 hash 报错，低序号拒绝；只有完成整次事务才持久化最高序号。
4. 对缓存实际 bytes 验证后，把已验证 hash 集合交给 planSync。不要凭对象文件名认为缓存正确。
5. 缺对象从多镜像相同 objects/hash 路径获取；大小和 SHA256 均正确才缓存。镜像缺失/损坏就换源。
6. 从目标完整 files 构造新 generation；这自然表达删除、重命名和移动，也支持直接跳过多个版本。
7. 验证所有文件完备后，用单一原子存储记录切换 active generation + accepted sequence/hash。
8. 后续重建索引/搜索缓存、刷新 UI；旧 generation 等没有读者后再清理。

Android/iOS/桌面需验证 rename/文件系统耐久性；Web 需用真实浏览器测试 CORS 与事务。
不要在 Flutter Web 中自行加需预检的 Authorization 或自定义 Cache-Control 请求头；GET + query nonce 即可。
下载地址只由已配置 baseUrl 与协议规定的相对路径组成，禁止照未签名 JSON 中任意 URL 下载文件。
GET bytes 需设置超时和响应上限，禁止把 Markdown 字符串重编码再计算哈希；UTF-8 BOM/CRLF 属于对象原始字节。

minAppVersion 必须设置为**第一个真正支持此协议和业务 schema 的客户端发行版**。
生产数据将 1.0.0 保留为首个 v1 客户端版本下限。现有 Flutter 为 0.0.263，尚未集成此协议；
这不是现有 APK 的兼容声明。客户端集成时必须先协调实际发行版本，再发布支持该 schema 的客户端。
轮换公钥先发客户端，再切发布钥；未知 keyId 时提示升级，保留本地库，不下载公钥就自动信任。
初装没有最高序号时无法辨别被服务端持续提供的旧有效签名（freeze attack）；不对 UI 承诺不可实现的“绝对最新”。

## 原 APK/网页构建迁移清单（待协调，不自动执行）

| 文件/行为 | 客户端变更 |
| --- | --- |
| .github/workflows/release_production.yml | 去掉两个私有源 checkout 和复制 Markdown/生成资产索引步骤；保留已有版本、Android 签名和 APK 发布逻辑 |
| .github/workflows/release_alpha.yml | 同样解耦源资料；任何 source dispatch 不指向该构建工作流 |
| .github/workflows/deploy_web_pages.yml | 数据获取改为客户端运行时协议；网页代码有变化才构建，不用资料变化驱动 Web 重编译 |
| pubspec.yaml | 引入审核后的同步/密码库依赖；资产可保留一次已验签基础库用于初装离线，不随资料日更构建 |
| ObsidianDataService / PreferencesService | 实现官方 generation provider 和 accepted sequence/hash；保留用户 overlay 与现有标签检索语义 |
| DB_VERSION dart-define | 改成读取已接受 manifest.dataVersion 和离线状态，不把 APK 编译期字符串作为当前资料版本 |
| Settings / 日志界面 | 展示 dataVersion、sequence、更新时间、镜像状态、下载进度和重试入口；无账号系统 |
| 私有读取 token | 客户端完成迁移后从 APK 构建流程中移除数据读取凭证；只在 RSTONE_Data 的受保护环境使用 |

当前两个 APK Release 工作流是 workflow_dispatch；本项目未新增任何指向 Flutter 的触发。
两个源库已按配置请求安装独立的数据通知工作流，源文件组织保持不变；没有增加 APK 通知。
客户端验收至少覆盖初装离线、重复更新、下载断网、进程退出、磁盘不足、镜像延迟、删除与移动、
自定义资料保护、升级最小版本/密钥轮换及真实 Web CORS；本项目测试不能代替这些设备验收。

Dart 示例使用 [cryptography Ed25519](https://pub.dev/documentation/cryptography/latest/cryptography/Ed25519-class.html)
及 [Unicode NFC](https://pub.dev/documentation/unorm_dart/latest/unorm_dart/nfc.html)，用完整 case-fold 表与 Python 发布器保持路径冲突判断一致。
