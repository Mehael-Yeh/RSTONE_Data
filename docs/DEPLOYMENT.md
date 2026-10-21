# GitHub Pages 与镜像部署

## 公开范围和外部配置

公开代码仓库：[Mehael-Yeh/RSTONE_Data](https://github.com/Mehael-Yeh/RSTONE_Data)，默认分支 `main`。
代码仓库和两个私有源库分离。正式发布真实源数据前审核 `SOURCE_AUDIT.md`，
确认工程师姓名、配方、供应商等原文字段均允许公开。对象、Git 历史、Actions 报告均不可视为保密存储。
本项目不修改上游仓库的组织、权限或分支，不部署业务数据库，不创建账号系统。

代码仓库和生产配置已创建；实际发布验证记录另见 `LIVE_DEPLOYMENT.md`。新环境按以下清单配置：

1. 已完成：创建公开仓库，把发布代码推送到默认 `main` 分支。
2. Pages → Build and deployment → Source 选择 **GitHub Actions**。
3. 创建/保护 `github-pages` environment，只允许默认分支运行；限制环境和工作流文件的修改权限。
   若要求全自动发布，不设置每次必须人工审核的 environment reviewer，使用分支保护和受信发布代码保证授权。
4. 当前使用两个独立的只读 SSH deploy key：`RSTONE_SOURCE_READ_KEY` 和 `TDS_SOURCE_READ_KEY`，
   私钥仅在数据仓库的 `github-pages` environment Secrets 中，公钥分别注册在对应源仓库。
   `scripts/setup_deploy_key.py` 直接经 stdin 写入 Secret，不落地私钥，不覆盖活跃 Secret；安装失败撤销公钥。
   每个密钥只能读取一个源库，不使用个人账号的广泛权限凭证。若未来改用 `PRIVATE_REPO_READ_TOKEN`，
   则使用只对两个源库 Contents: read 的 fine-grained PAT/App token，并同步更改 checkout 输入。
   Actions 默认 GITHUB_TOKEN 无法读取另一个私有仓库。
5. 生成 Ed25519 seed，并通过 stdin 写入 environment Secret `DATA_SIGNING_PRIVATE_KEY`。
   用 `python scripts/setup_signing.py --repository Mehael-Yeh/RSTONE_Data --key-id production-2026`
   将私钥直接送入 GitHub Secrets，同时生成只含公钥的 config 文件变更。脚本不会把私钥写入文件或日志。
   审核并提交 `config/trusted_keys.json` 的公钥，再设置 environment variable `DATA_SIGNING_KEY_ID=production-2026`。
   `DATA_SIGNING_SECRET_NAME` 初始可不设（默认 DATA_SIGNING_PRIVATE_KEY），轮换时选择独立的新 Secret，不能先覆盖活跃私钥。
6. 审核实际发布范围后设置 environment variable `PUBLIC_DATA_APPROVED=true`。
   生产公钥已记录在 `config/trusted_keys.json`；未配置匹配私钥时生产失败，本地测试自己生成临时密钥。
7. 给此仓库 GITHUB_TOKEN 开启 Contents: write、Pages: write、id-token: write（工作流中已最小化声明）。
   默认 token 权限保持 read，不能批准 PR。默认分支要求 PR，只有所有者可合并；distribution 禁止
   force push 和删除，工作流正常 fast-forward 写入。不要给通知凭证 Contents/Workflows 写权限。
8. 第一次手动运行 Publish RSTONE Data，验证报告、签名、公网对象、CORS 和 latest。
9. `data-operations` environment 只允许 main，required reviewer 为 Mehael-Yeh。该环境没有 Secrets。
   仅回滚、非空 deletion_approval 和历史清理需要先经过这个审批任务；普通发布自动跳过该任务。
   个人仓库允许所有者审核自己发起的操作。通知令牌只有 Actions:write，不能调用需要 Deployments:write
   的审批 API，不能修改 environment 规则。通知令牌**不得授予 Deployments:write/Administration:write**。
   参考 [部署审批权限](https://docs.github.com/en/rest/actions/workflow-runs#review-pending-deployments-for-a-workflow-run)。

首次建仓的创建/推送命令如下，当前仓库已完成，勿重复执行创建命令：

```text
gh repo create Mehael-Yeh/RSTONE_Data --public --source . --remote origin
git push -u origin HEAD:main
gh repo edit Mehael-Yeh/RSTONE_Data --default-branch main
```

标准入口为 `https://mehael-yeh.github.io/RSTONE_Data/latest.json`；上线和验证状态见 `LIVE_DEPLOYMENT.md`。
自定义域名应在 Pages 设置中另行配置，HTTP 重定向不被校验器接受，填写最终 HTTPS baseUrl。

## 事务和状态分支

`main`：发布代码、可信公钥、协议、测试和模拟示例；不存放真实私有源快照。
`distribution`：`public/` 下的正式静态资源，`state/pending.json` 和结构化历史审计报告。
**只上传 public/ 到 Pages**，禁止把源码工作区、源库、state 或 .git 上传为站点。
分发分支有单独的 `.gitattributes`，public 和 state 均禁止 Git 换行转换，保证签名/哈希跨 Windows 和 Linux 不变。

串行工作流（不取消进行中的发布）：

1. 运行本地协议测试、检查公开范围批准，拉取准确源分支；失败不使用空数据或 Flutter assets 代替。
2. 生成缺失对象、完整 manifest 和签名；本地校验后将候选及 pending commit 到 distribution。
3. 第一次 Pages 部署保留旧 latest。首次发布时暂时没有 latest。
4. 从刚部署的公开 URL GET 全部目标对象、清单和签名，验证内容以及匿名 CORS 响应头。
5. 最后更新本地 latest，连同 pending checkpoint 到 distribution，执行第二次 Pages 部署。
6. 再从公开 URL 验证 live latest 正好等于候选且全部对象有效，才清除 pending 并记录完成。

中途失败：旧 latest 保持，或已推进到一个全部引用都校验通过的有效新版本；pending 保证下次优先恢复，
不因源内容没变而跳过尚未完成的最终部署。pending 恢复不依赖上游当时是否还能访问。
并发工作流通过同一 concurrency group 串行；Git push 无 force，意外并发写入会失败，不覆盖其他提交。
Pages/CDN 部署不能提供全球边缘强一致性，客户端下载协议始终需要事务和重试。

无变化运行不签发新版本、不写入新对象、不部署站点，报告本次观察到的两组 commit。
首次缺少任一类资料、整体空库、预期目录缺失、签名无效、异常删除未批准都会失败。
已有版本有意清空某类资料需精确批准；Rstone 仍需保留预期目录的受跟踪占位文件。
每 6 小时兜底检查（UTC 的 00:23、06:23、12:23、18:23，即北京时间 08:23、14:23、20:23、次日 02:23），
GitHub schedule 可能延迟，不承诺准点。repository_dispatch 只作为通知，发布器重新读取固定分支，不信任 payload 提供的数据或命令。

## 跨源仓库通知

已安装的通知基于 `examples/upstream-dispatch.yml.example`。Rstone 使用 `master` 和三个 Markdown
目录过滤；RstoneTDS 使用 `main` 和根目录 `*.TDS.md` 过滤。也支持手动通知，用于连接验收。

每个源库的 `rstone-data-notify` environment 只允许自己的默认分支访问，Secret 名为
`DATA_DISPATCH_TOKEN`。通知调用目标的 **workflow_dispatch**，固定 `publish-data.yml` 和 `main`，
不传入回滚或删除批准参数，不读取或上传源内容。发布器自行读取两个固定的最新源分支。

一次性的外部凭证配置：

1. 在 GitHub Settings → Developer settings → Personal access tokens → Fine-grained tokens 创建令牌。
2. Resource owner 选择 Mehael-Yeh，Only select repositories 只选 **RSTONE_Data**。
3. Repository permissions 只授予 **Actions: Read and write**，其余保持默认 Metadata: read；
   **不授予 Contents、Workflows、Deployments、Administration 写权限**。指定到期时间并安排轮换。
   也可用相同权限的 GitHub App token。
4. 分别在 [Rstone environments](https://github.com/Mehael-Yeh/Rstone/settings/environments) 和
   [RstoneTDS environments](https://github.com/Mehael-Yeh/RstoneTDS/settings/environments) 的
   `rstone-data-notify` 保存 `DATA_DISPATCH_TOKEN`，然后手动运行各源库的 Notify RSTONE data publisher。

令牌由 GitHub 用户本人创建；当前 gh OAuth 登录不能通过公开 API 代建 fine-grained PAT。
缺少令牌时通知任务明确失败，但数据仓库的手动发布和六小时兜底检查正常工作。
参考 [workflow_dispatch 所需 Actions: write](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)。
保留 repository_dispatch 类型 rstone-data-changed，供未来已有受限 App 的部署使用；该 API 需要
Contents: write，因此当前默认通知不采用它。两种方式都不触发 Flutter 工作流。

安装时曾实测 SSH 通知替代方案，发现 deploy key 会继承创建者规则例外，GitHub 内置发布 token
也不能按普通用户 actor 绕过 distribution 限制。该方案已撤回：两枚通知 write key、Secret、信号分支和
接收工作流均已删除，只保留实际需要的两枚**只读源密钥**。没有把个人账号的广泛权限 token 复制到 Actions。
源只读 deploy key 无自动到期，应定期轮换；撤销创建它的 OAuth 授权也可能撤销密钥，
见 [GitHub deploy keys](https://docs.github.com/en/rest/deploy-keys/deploy-keys)。

## 镜像合同

已验证的两个入口见 `config/mirrors.json`。备用入口是 distribution/public 的 GitHub raw，和 Pages
共用 GitHub 基础设施；它提供不同访问端点，不作为独立服务商容灾或国内加速的承诺。
raw 入口直接复用状态分支中的相同签名与对象。分支提交可原子包含对象和指针，但 CDN 仍可能滞后；
客户端需按协议校验、切换镜像和保留旧 generation。

镜像必须复制同一批已签名的原始字节，不重新生成版本、JSON 或签名。只改变 baseUrl。
对象和不可变清单先复制，在目标公开 HTTPS 上验证指定候选（`verify --pointer-file`），最后写 latest。
`python scripts/mirror.py --source-store <store> --target <镜像站点目录> --base-url https://mirror.example/data --keys config/trusted_keys.json`
实现本地挂载站点目录的这个流程；远程 S3/CDN 上传器可复用相同顺序和校验器。
镜像保持旧资源以容纳 CDN 滞后，不立即删除历史对象。

GitHub Pages 响应头由 GitHub 管理，仓库 `_headers` 不能配置缓存/CORS。
发布工作流 GET 时检查 `Access-Control-Allow-Origin: *`，缺失就停止推进指针。
自有 Nginx/CDN 配置参考 `examples/nginx.conf`：对象/清单长缓存，latest 短缓存；全部允许匿名跨域 GET。
用 `python scripts/data.py verify --base-url <baseUrl> --require-cors --keys config/trusted_keys.json`
验证完整站点，不能只用 HEAD 检查 HTTP 200。
浏览器另在 Flutter Web 测试环境执行跨源 fetch(latest → manifest → sig → object)，检查 CORS 控制台。
客户端始终校验签名和对象原始字节，不依赖 MIME 类型（Pages 的无扩展名对象可能为 octet-stream）。

镜像 lag：客户端选择可验证目标完整清单，同哈希对象可跨镜像下载；某镜像 404 时换源，不回退本地已接受序号。
全部镜像缺失任一对象时保留旧数据，报告可重试错误，不删除旧官方文件。
Pages [站点上限 1 GB、带宽软限 100 GB/月](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)，
需监控历史对象增长；辅助 ZIP 也占空间。规模增长后再增加对象存储/CDN，协议不变。
