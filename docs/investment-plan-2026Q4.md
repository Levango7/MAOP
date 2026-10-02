<!-- docs-gate: exempt=规划施工图（未并入 ROADMAP 评审），计划态引用多，暂免一致性检查；并入后应删除本豁免 -->
# 2026 Q4 投入计划：三大工作流细分设计（施工图）

> **定位**：本文是 2026-09-30 体检后"投入决策"的落地施工图，覆盖三个工作流：
> WS-1 商业锁硬化（MAOS）、WS-2 工程债清偿（MAOP）、WS-3 ROADMAP 主线施工。
> 来源：2026-09-26 全面评估 P1 残留 + 2026-09-29/30 复查新发现（含两个断 API 哑弹）。
> **与 ROADMAP.md 的关系**：本文是细分施工设计；各任务开工时应把范围与验收标准并入
> `ROADMAP.md` 对应版本节（v5.2.x patch / v5.3.0），此后以 ROADMAP 为准，本文降级为施工记录。
> 所有事实声明都经过 2026-09-29/30 代码核实，引用格式 `文件:行号` 以当时 master 为准。

## 0. 设计原则

1. **先修断的，再建新的**。已上线但一调就 500 的 API（T3.0）优先于一切新功能。
2. **诚实分级**。商业锁的目标是"把对抗成本抬高到不划算"，不承诺绝对防逆向；
   每项安全任务都要在 README Known Limitations 里同步写清"防到哪、不防哪"。
3. **验收必须非 mock**。v5.2.0 的教训：E2E 全 mock 出来的绿灯掩住了两个断 API。
   集成验收一律用真实组件（临时目录 / 真 SQLite / kind 集群），mock 只到外部边界（LLM、Redis 可用 fakeredis + 一个真容器冒烟）。
4. **变异验证惯例延续**：关键守卫测试要证明"改回 bug 就红"。
5. **发布纪律**：走 CHANGELOG 既有节奏规范（patch 每周≤1，minor 双周≤1）。

---

## 1. WS-1：MAOS 商业锁硬化（从"防君子"到"抬高对抗成本"）

**现状一句话**：文档化旁路已全部关闭（edition 旁路 / env 逃生舱 / state fail-closed，09-28 落地），
但 README Known Limitations #4/#7/#8/#9/#10/#11 的结构性弱点原样存在：
无键哈希链可整体重算、CRL 出厂弱模式、SSO 会话单节点、时钟回拨无防护、
无混淆无在线激活（进程内攻击者想改什么改什么）。

### T1.1 审计哈希链加键（P1 · 规模 M）

**问题**：`audit.py:65-76` 的 `audit_chain_digest` 是无键 `sha256(prev + "|" + fields)`，
有 DB 写权限的攻击者改一条后可重算其后整条链，`verify_chain` 无法区分（README #10）。
PG 侧排序靠 `ctid` 决胜（`pg_persist.py:380-416`），`VACUUM FULL`/行移动即失真（代码自认）。

**设计决策**：
- **双层防篡改**：
  - **L1 键控链（v2 链）**：`audit_chain_digest_v2 = HMAC-SHA256(key, prev_hash + "|" + fields)`。
    键来源 = 从 license 派生（`HKDF(license 材料 + install_id)`，install_id 首次启动生成存 `data/`），
    客户内审计员可验（持有 license 即可重算），防"只有 DB 写权限"的攻击者。
    事件头加 `chain_version` 字段；`verify_chain` 按 v1/v2 双读，v1 链验到 v2 首条为止（平滑迁移）。
  - **L2 链头锚定（防"连 key 一起改"）**：新增 checkpoint 机制——每天（或每 N 条）把
    `{chain_head_hash, last_event_id, ts}` 导出为待签文件；两个锚定后端：
    a) `scripts/audit_checkpoint.py` 导出 → 人工/定时送 RFC3161 TSA 时间戳（标准协议，无供应商锁定）；
    b) 预留 POST 到 vendor 端点的接口（与 T1.6 激活服务同部署，后做）。
    校验时可用历史 checkpoint 验证"链头在 T 时刻已是这个值"，事后重算整条链无法伪造历史 checkpoint。
- **ctid 脆弱性缓解**：审计表加 INSERT-only 触发器（`BEFORE UPDATE/DELETE → EXCEPTION`）+
  运维文档注明"禁 VACUUM FULL on audit 表"（或改用 `pg_repack`）；`verify_chain` 检测到
  ctid 断裂时输出区分性 reason（"chain fork OR rows moved — check VACUUM FULL"）。
- **键轮换**：checkpoint 文件携带 key_id；支持多 key 并行验证窗口。

**子任务**：
1. `audit.py`：v2 digest + `chain_version` 头 + 键派生（HKDF）与 install_id（MAOS 侧，S）
2. `pg_persist.py` / `SqliteAuditStore`：写侧 v2、`verify_chain` 双读、区分性 reason（M）
3. checkpoint 导出脚本 + RFC3161 时间戳封装（用 free TSA 如 freetsa.org 起步，可配）+ 校验命令（S）
4. INSERT-only 触发器迁移（Alembic，MAOP 侧 migrations）+ 文档（S）
5. 攻击模拟测试：重算攻击（改 1 条+重算全链）在 v2 下必须被检出；v1 旧链兼容测试（M）

**验收**：重算攻击测试红转绿；既有审计测试零回归；README #10 改写为 v2 语义与残留边界
（持 key 者仍可重算——需 L2 锚定，锚定是运维流程）。

### T1.2 CRL 出厂安全化（P1 · 规模 S）——✅ 代码完成 2026-09-30，落分支 `fix/t1.2-crl-strict-default`（MAOS 仓）

**执行记录**：production（`MAOP_ENV=production`）且未显式设置 `MAOP_CRL_STRICT`
时默认 strict——CRL 不可达且无缓存即拒绝（fail-closed）；显式 `=0` 降级仍被尊重
但记 error 日志留痕；开发/测试宽松默认不变。5 条测试 + README #3 改写 +
MAOS CHANGELOG 记账，ruff clean，全量 200 passed（唯一红灯=清单守卫，预期内）。
**并入 master 的前置**：用生产私钥重签 manifest（`scripts/sign_enterprise_modules.py
--key <生产私钥>`）——私钥按 36daaff 纪律不在仓内，本地浅搜未获，等持有方执行。
遗留：CRL 状态暴露到 license 状态端点（子任务 2）顺延。

**问题**：`MAOP_CRL_STRICT` 默认 0（`crl.py:105,142-146`），非严格下"CRL 服务不可达即放行"
（`crl.py:173-182`）——出厂行为等于撤销检查可被网络手段绕过（README #3）。

**设计决策**：**production 默认 strict**：`MAOP_ENV=production` 且 env 未显式设置时
`strict=True`；显式 `MAOP_CRL_STRICT=0` 降级为弱模式时写 WARN 审计日志（谁在何时关的，可追责）。
availability 兜底不变：strict 下靠既有缓存（TTL 3600s / max-age 7d）扛 CRL 服务抖动；
另把 CRL 状态（上次成功拉取时间、缓存年龄、strict 与否）暴露到 license 状态端点，
dashboard 可见。非 production 语义不变（测试/离线开发不联网）。

**子任务**：默认值反转（含 env 未设/设 0/设 1 三分支测试，S）；状态暴露（dashboard license
端点加字段，S）；README #3 更新（S）。**验收**：production 下拔网线 → license 拒绝（缓存过期后），
测试锁定。

### T1.3 SSO 会话 Redis 后端（P2 · 规模 M）

**问题**：`sso_session_store.py` 仅 SQLite（README #7），多副本部署会话不共享。
插入点已备好：docstring TODO（`sso_session_store.py:24-26`）；两个消费方
（`SSOManager` sso.py:158-170 / `SSOProviderRegistry` sso_registry.py:110-113）都接受构造注入，
`maybe_open_store`（:242-259）按 env 分支即可。

**设计决策**：新增 `RedisSSOStore`，方法签名与 `SqliteSSOStore` 逐一对齐
（`save/get/delete_session`、`purge_expired`、`save_pending/pop_pending/gc_pending`）。
要点：TTL 用 Redis 原生 `SETEX`/`PEXPIREAT`（expires_at 映射），`pop_pending` 的原子取出用
`GETDEL`（Redis ≥6.2）或 Lua 兜底；值沿用 Fernet 加密（复用 `_resolve_master_key`，
`enc:` 前缀）——Redis 被读不等于会话泄漏；`maybe_open_store` 对 `redis://` 前缀返回新实现。
**故障语义（决策点）**：Redis 不可达时登录 fail-closed（拒绝新登录），已建立会话的读失败
也 fail-closed（401），不做内存回落——多副本场景回落到单节点内存等于静默回到缺陷态。
连接参数：`MAOP_SSO_REDIS_URL` + 超时/池大小。

**子任务**：实现（M）+ fakeredis 单测 + docker-compose 加 Redis 的真容器冒烟（S）+
README #7 更新与部署文档（S）。**验收**：双副本集成测试（两个进程实例共享会话互通）。

### T1.4 时钟回拨防护（P2 · 规模 S-M）

**问题**：license 过期判断只看本机时钟（README #8），回拨即续命。

**设计决策**：单调水印 + 宽容窗，不做"拒启动"做"拒授权"：
- 持久化水印 `{last_max_wallclock, key_id}`，HMAC（键同 T1.1 install 派生键）防直接改文件；
  每次校验事件（license validate / SSO 登录）更新 `max(now, watermark)`。
- 检测到 `now < watermark - 容忍窗`（默认 24h，NTP 抖动/夏令时不算）→ production 拒绝
  license 相关授权并返回明确错误码（`LICENSE_CLOCK_ROLLBACK`），审计记录；
  非 production 仅告警。
- **VM 快照回滚的合法场景**：管理员 CLI `maop-enterprise unlock-clock` 生成带理由的
  审计事件并重置水印（MVP 本地即可；与 vendor 会签的解锁流程留 T1.6 服务上线后做）。

**子任务**：水印存取 + HMAC（S）；license 校验接入 + 错误码 + 测试（S）；CLI + 文档（S）。
**验收**：模拟回拨 48h → 拒绝；回拨 1h → 正常；水印文件被篡改 → 检出。

### T1.5 核心代码二进制化：Cython AOT 自建管线（P2 · 规模 M-L）

**问题**：.py 明文随 wheel 分发，进程内/文件级攻击者可任意改代码（README #4/#11 的根）。

**方案选型（2026-09-30 调研结论，采购路线已否决）**：

| 路线 | 成本 | 保护强度 | 结论 |
|---|---|---|---|
| PyArmor Pro 采购 | ~$89-158 + license 管理 | 字节码 VM，反混淆工具链存在 | **否决**（用户决策：不采购）；且它本身就是国产商业工具，无"更国产"的替代品——调研未发现成气候的国产 PyArmor 替代产品 |
| **Cython AOT 编译 .pyd/.so** | 0（开源 Apache） | **无字节码可反编译**，等同原生软件的逆向门槛 | **主选**：社区成熟标准打法（"核心编二进制 + 薄壳入口"），我们自建构建管线即"自己造土壤" |
| Nuitka | 0（开源 Apache-2.0） | 同为 AOT，偏整程序打包 | 备选：适合将来做单二进制交付形态，wheel 库分发不如 Cython 贴合 |
| 自研字节码加密加载器 | 1-2 周开发 | 弱——密钥必在本地，内存 dump 可取回代码对象（PyArmor 本质就是这思路的商业强化版） | **否决**：同样的开发量，Cython 路线防护高一个量级 |
| Virbox/加密狗（深盾等国产） | 商务谈判 | 授权硬件强绑定 | **不选**：面向二进制程序加固+加密狗授权，对 Python 库形态过重；留作将来高敏感客户的加购选项 |

**设计决策**：
- **范围（薄壳模式）**：`maop/enterprise/**` 中的**业务逻辑核心**全部编译——license 校验、
  公钥/指纹、CRL、audit 链、sso session store、rbac 引擎、crypto 工具（这是防破解的价值所在）；
  **pydantic 模型与 FastAPI 路由层保留薄壳 .py**——Cython 编译会丢 `__annotations__`，
  FastAPI 依赖注入与 pydantic 校验靠它（`embedsignature=True` 只回补签名字符串，
  不回补注解，FastAPI 仍然不工作）。薄壳里不放逻辑，只有声明与转发，泄密面趋近于零。
- **构建管线**：`scripts/build_enterprise_binary.py`（cythonize + setuptools 产 per-platform
  wheel）；CI 矩阵三条腿（Linux/macOS/Windows——现有 CI 已有此矩阵）各产一个
  enterprise wheel，cibuildwheel 编排；`pyproject.toml` 的 enterprise extra 不再加 Cython 依赖
  （客户只装产物，构建机才需要）。
- **发布链顺序（关键，与混淆版同理）**：源码 → cythonize 编译 → **对编译产物**（.pyd/.so +
  薄壳 .py）收集哈希 → 签完整性 manifest → 构建 wheel。注意：现 `collect_module_hashes`
  只收 `*.py`（`test_integrity.py` 断言 `key.endswith(".py")`），需扩展为"产物感知"
  （编译产物为主、薄壳 .py 为辅），`verify_module_integrity` 的基准目录逻辑同步改。
- **诊断代价（向客户披露）**：核心模块 traceback 变成二进制帧，runbook 增加
  "编译制品日志如何解读"；`embedsignature=True` 保住函数签名可读性。
- **兼容审计前置（spike，1-2 天）**：先对 enterprise 全量跑一次 cythonize + 全量测试，
  逐模块记录不兼容点（预期：pydantic 模型类、含大量运行时反射的 sso_registry；
  一般解法是这些退回薄壳或局部改写）。审计结论决定"编多少"——目标底线：
  license 校验与 crypto 主链必须全二进制。

**子任务**：兼容审计 spike（S）；构建脚本 + 三平台 wheel 管线（M）；manifest 产物感知改造
+ 发布守卫适配（M）；CI nightly 编译制品全量测试腿（S）；薄壳层改造 + README/runbook（S）。
**验收**：三平台编译 wheel 安装后全量测试绿（MAOP 主仓 + MAOS）；发布守卫绿；
wheel 内 `license.py`/`crl.py`/`sso_session_store.py` 等核心无 .py 明文（构建产物清单断言）；
decompyle3/uncompyle6 对制品无能为力（人工抽验一次留证）。

### T1.6 在线激活（P3 · 规模 L；M1 离线激活 = M）

**问题**：license 是离线签名文件，无设备绑定、无 seat 控制，一份 license 可无限拷贝。

**设计决策（分三期，MVP 不需要服务器）**：
- **协议**：activation request（`license_id + device_fingerprint(机器标识多因子哈希) + 随机数`）
  → activation token（vendor Ed25519 签名：license_id + fingerprint + 签发/过期时间 + seat 序号）。
  客户端 `validate()` 在 production 下要求有效 activation token（宽限期
  `MAOP_ACTIVATION_GRACE_DAYS` 默认 14 天，给迁移/换机缓冲）。
- **M1（离线激活，M）**：客户 CLI 导出请求文件 → vendor 用签发工具（本地私钥仪式，
  复用现有密钥轮换流程）签出 token 文件 → 客户导入。air-gapped 私有化客户友好，
  无新基础设施。seat 数在签发工具侧手工台账。
- **M2（激活服务，L）**：vendor 托管 FastAPI 小服务（seat 计数 DB + 签发 API +
  吊销联动 CRL）；客户可选在线激活（自动）或继续离线文件。
- **M3（自助门户，L）**：客户后台自助激活/解绑/换机，频控防滥用。

**子任务（M1）**：设备指纹函数（多因子，虚拟机场景可配）+ 请求/令牌格式与签名（M）；
客户端校验接入 validate + 宽限期 + 测试（M）；vendor 签发 CLI（S）；文档（S）。
**验收**：换机必须在宽限期后失效；token 被篡改/过期/指纹不符一律拒绝；离线全流程演练一遍。

### WS-1 排序

```
T1.2(S) ──┐
T1.1(M) ──┼── 并行开（都是纯 MAOS 代码，无互相依赖）
T1.3(M) ──┤
T1.4(S) ──┘
T1.5(M-L) ── 先跑兼容审计 spike，结论决定编译范围
T1.6 M1(M) ── 最后（其设备指纹/键派生复用 T1.1 产出）
```

---

## 2. WS-2：MAOP 工程债清偿

### T2.1 dry-run 信号产出方（P2 · 规模 M-L）

**问题**：`_gate_dry_run`（`maop_verify.py:131-237`）是"验信号"不是"做预演"；
执行链 `Dispatcher.dispatch`（`dispatch_core.py:87-99`）不收 plan/dry_run，五种 driver
（`drivers.py:732-742`）全是 subprocess、无预演模式；`MAOP_DRY_RUN_ENFORCE=1` 现状=三路由全卡 verify。

**设计决策**：
- **信号通道 = stdout 结构化标记**。跨子进程唯一现实通道；同时**收紧误报面**：
  现合同是大小写不敏感子串匹配 `"dry-run"`（任务文本提一句都会假触发），
  改为要求行首哨兵 `MAOP_DRY_RUN_MARKER: {"dry_run": true, "artifacts": [...]}`（JSON 行），
  旧三种子串保留为弃用兼容（打 WARN），一个 minor 后移除。合同变更是我们自己的
  gate，同步改 `maop_verify.py` docstring 与 `docs/configuration.md`。
- **传递链**：`Plan.dry_run` → `loop_executor.py:314` → `dispatch(dry_run=...)` 新参 →
  driver 注入 env `MAOP_TASK_DRY_RUN=1`（五种 driver 统一，外部 CLI 想支持就有的读）。
- **两类执行器分别落地**：
  - pipeline（doc-pipeline 适配器，python 驱动子进程，`doc_pipeline_adapter.py:132-173`）：
    我们自己的代码——给 `run_pipeline` 加 `dry_run` 穿透到 `orch.run`（或跳过写侧步骤），
    输出哨兵行 + 预期产物清单。这是能"真预演"的主战场。
  - fileops（qwenpaw 等外部 agent CLI）：`config/agents.yaml` 加能力声明
    `dry_run_supported: true`；enforce 只对声明能力的 agent 生效（未声明的路由 enforce 不置
    dry_run，写 WARN 提示）。避免"外部 CLI 根本不懂 dry-run 还被闸门卡死"。
- **默认开启决策**：以上齐 + e2e 两条（pipeline/fileops 各一）后，改默认
  `MAOP_DRY_RUN_ENFORCE=1`（minor 版本 + CHANGELOG 行为变更段）。

**子任务**：合同收紧 + 兼容（S）；传递链 + env 注入（M）；doc-pipeline 真预演（M）；
agents.yaml 能力声明 + enforce 条件化（S）；e2e + 默认开启（S）。
**验收**：enforce=1 下 pipeline 任务真预演通过、写操作零副作用（临时目录断言）；
未声明能力的外部 agent 不受影响。

### T2.2 requirements.lock 真锁（P1 · 规模 S-M）——✅ 完成 2026-09-30

**执行记录**：新增 `py/requirements.frozen.txt`（uv `pip compile --extra enterprise
--universal`，45 包全 `==` 带溯源）；pip-audit / SBOM(cyclonedx) 输入与 pip 缓存键
全部换到 frozen 文件（此前审"范围"= 审"能落进范围的任意版本"）；新增 `lock-drift`
CI 作业（重编译剥注释按内容比对）并纳入 merge gate `REQUIRED_UPSTREAM`；
守卫测试 `test_frozen_lock.py` 3 条；`requirements.lock` 退为直依赖镜像参考
（镜像守卫不变）。`ml` extra 未入锁（torch 链 universal 解析失败 + 非生产运行时，
文件头记录）。旧 T2.2 设计里的"uv lock / pip-compile 二选一"落定为 uv pip compile。

**问题**：lock 下半段传递依赖是手写 `>=` 范围（CHANGELOG 台账自认"reference，不是真锁"）。

**设计决策**：用 **uv lock**（或 pip-compile，二选一，倾向 uv：快、跨平台、单文件）。
`uv lock` 生成全量精确 pin → 落 `requirements.lock`（保留现有直依赖镜像守卫测试，
另加"传递依赖必须 `==` 精确 pin"断言）；CI 加 lock 同步 job：依赖相关 PR 上重生成并
`git diff --exit-code`，drift 即红；dependabot 升级 PR 会自动带上 lock 变更。
Python 3.10 兼容注意沿用既有守卫测试的口径（不用 3.11+ 标准库特性）。

**子任务**：生成脚本 + 首次全量生成（S）；守卫测试升级（S）；CI job（S）。
**验收**：改 pyproject 不改 lock → CI 红；lock 可在干净 venv 一键复现环境。

### T2.3 `TestCallSyncFallback` flaky 抓现行（P1 · 规模 S，时间盒 2 天）——◐ 阶段一完成 2026-10-02（止血），泄漏源归位仍是开放题

**进展**：CI 现行（run 36792737281 macOS 腿 + Nightly 同族）+ 探针拍全现场——
污染源=存活到本文件执行期的全局 `subprocess.run` MagicMock
（`run_impl=MagicMock@unittest.mock`，最早污染边界=TestCallSyncFallback 执行期间）；
全库常规 patch 用法排查无作用域破绽，头号嫌疑=异步/后台线程路径上未 unwind 的
with-patch（同条探针带出残留线程 `mcp-adapter-bg`）。**止血已落**：
`test_tool_manager.py` 模块级密闭守卫 `_hermetic_subprocess_run`（每用例 setup
恢复标准库原版，本文件免疫）+ `TestHermeticGuard` 两条变异可证自检；探针在守卫
之前执行，追凶线索保留。**剩余**：找到泄漏源本身（守卫/探针告警即线索），
时间盒续期见 CHANGELOG 2026-10-02 段。

**问题**：台账自认"三元凶的机制还没抓到现行"，探针已收敛到只报真嫌疑人。

**设计决策**：时间盒 spike，超时即降级止损：
1. 复现 harness：本地循环 N 次 + `faulthandler.dump_traceback_later` +
   既有泄漏探针输出全部落盘（S）
2. nightly stress job：该文件重复跑 10 遍（`pytest --count` 或循环），抓到现行
   → 定位修复 + 回归测试；抓不到 → 两条止损：①标记 `xfail_strict=False` 的
   rerun 策略写进 CI 已有 rerunfailures 语义，②台账记录"未复现条件"与复现配方（S）

**验收**：连续 7 天 nightly 零假红（修复路线），或止损方案台账化（降级路线）。

---

## 3. WS-3：ROADMAP 主线施工

### T3.0 v5.2.0 哑弹排除（P0 · 规模 S）——✅ 已完成 2026-09-30（未提交）

09-29 核实的三个实质缺陷（都在 `dashboard/services/evolution_service.py`），**已修复**：
1. `:637` `await loop.run_cycle(...)`——`run_cycle` 是同步 def（`evolution_loop.py:139`），
   `POST /api/evolution/loop/trigger` 一调即 TypeError → 500；
2. `:698` `loop._load_report(cycle_id)`——全包不存在，审批 API 一调即 AttributeError → 500；
3. `:702-705` approve 分支 `pass`——批准后不落库、不触发后续。

**修复内容**：
- `trigger_evolution_loop` 改 `asyncio.to_thread(loop.run_cycle, ...)`（修哑弹 + 不阻塞事件循环）；
- `EvolutionLoop` 新增公开 `get_report(cycle_id)` / `update_report(report)`——后者是 UPDATE
  语义（新发现的第 4 雷旧路径 `_save_report` 是纯 INSERT，对已存在 cycle 再保存会撞
  主键 IntegrityError）；service 弃用两个私有方法；
- `LoopReport` 新增 `approved_suggestions` / `rejected_suggestions` 字段（带默认值，
  旧 JSON 行兼容）：决策后 suggestion 从 pending 归入两支，全部决策完 approval_state
  收敛 approved/rejected/partial（枚举语义本就有 partial，此前无实现）；
- 审批错误路径契约补齐：非 pending 的 suggestion → ValueError（路由 400）。
- 测试：`test_evolution_loop_ac07.py` 删掉两个 mock 掩盖下的旧用例（正是哑弹所在），
  新增 4 条非 mock 回归（真 loop + 真 SQLite：trigger 落库断言、决策往返收敛、
  404/400 错误路径、get/update_report 契约）；演化全套 88 passed + e2e 7 passed，ruff clean。
- 遗留到 T3.1：approve 后建议如何回流到下一轮 APPLY（run_cycle 只消费当轮
  evaluate 的 approved 列表，跨轮回流仍是断的——本轮只保证决策正确持久化、状态可见）。

### T3.1 v5.2.0 四条验收收尾（P1 · 规模 M-L）

对应 `ROADMAP.md:145-148` 未勾四条：
1. **E2E 全链路（非 mock）**：mock 边界只留 LLM（录制响应或极小模型），其余真组件：
   observe→suggest→人工审批（走修好的 API）→APPLY→A/B→promote/rollback，
   workspace 用临时目录；现状 e2e 把 `_phase_*` 和 ChangeTracker 全 mock 了
   （`e2e/test_evolution_loop_e2e.py:132-137` 等），重写。
2. **劣化注入自动回滚 <5min**：把 `_build_degradation_test_suggestion`（现"仅供测试，
   生产无入口"，`evolution_loop.py:392-408`）接上真实入口（dashboard 按钮 + CLI），
   SLA 用真实 ChangeTracker 快照/回滚计时，替换现在 mock 空调用测出的假 300s。
3. **开关零回归**：已有 ac01 测试，补"开 + dry_run 关"组合的全量回归跑一次记录基线。
4. **dashboard 可见**：前端目前零页面调用 6 个新端点（`EvolutionHistory.vue:654-714`
   用的还是旧 `/api/evolution/cycles|ab/list|approve`）——新增 EvolutionLoop 面板
   （状态机流转 + 审批列表 + A/B 结果 + rollback 按钮）；后端"状态机"当前是从最近
   报告反推的静态标签（`evolution_service.py:601-614`），补内存运行时态（running /
   validating / rolling_back）。

**验收** = ROADMAP 四条逐条勾选 + 本节非 mock 约定。

### T3.2 M2.2 多模态记忆预研 spike（P3 · 规模 M，2 周时间盒）

PRD 4.3 的开工前置：嵌入扩展接口（`UnifiedMemoryProtocol` 加 modality 维度的兼容设计）、
pgvector 半精度/多向量列方案对比、融合检索（RRF 起步）PoC、KGE 选型对比
（图库选型：Neo4j Enterprise 成本 vs 纯 pgvector 演进路线）。产出：选型 ADR-0xx + PoC 仓库分支。
不做功能落地——那是 v5.3.0 的事。

### T3.3 K8s Operator 分期（P3 · 规模 L；M0 = M）

**现状**：CRD 三件套（maop.io v1alpha1 MaopAgent/MaopTask/MaopWorkflow）设计完整、
helm 图表齐、但零控制器代码（README 自述 PLANNED），集成测试腿在 CI 基本全 skip。
按 ROADMAP 属阶段三，但 M0 可以提前搭地基：

- **M0（M）**：kopf 骨架 + `MaopTask` reconcile 最小闭环（CRD → 调既有编排 API → status 回写
  phase/conditions）+ Dockerfile + `ghcr.io/maop/operator` 首次真实构建 + kind 集成腿激活
  （现有 L5-L7 skipif 钩子装了 kind 自动生效，不用改测试结构）。
- **M1（L）**：`MaopAgent`（Deployment+Service+replicas/quotas 映射）、RBAC 收敛、
  多副本与滚动升级。
- **M2（L）**：`MaopWorkflow`（CRD → MaopTask DAG 编排）、失败重试/补偿。
- **M3（L）**：多租户隔离 / RLS / 插件加载（把三个占位 skip 测试做实）。

**验收（M0）**：kind 集群 apply 一个 MaopTask → operator 拉起执行 → status.phase=Running →
任务完成 phase=Succeeded，e2e 在 CI 绿。

### T-UI 支线：顶栏 / 左侧边栏布局精修（P2 · 规模 S-M）——✅ 核心完成 2026-09-30（用户拍板方向已落地）

**用户拍板（09-30）**：顶栏左侧大标题；顶栏是顶栏不被侧栏盖；侧栏只占顶栏和
尾栏之间的内容带。已实现：侧栏 `top: var(--topbar-h)` 从顶栏下方开始（桌面
push 不变、移动 drawer 同样不遮顶栏）、顶栏大标题 `--fs-2xl`/800 + logo 放大、
品牌区常驻。新增 `e2e/layout.spec.js` 5 条布局契约（chromium 5 passed）+
`npm run build` 通过。**未做**：子任务 1 断点诊断/900-1100 默认 rail（等用户
补充当时的窗口宽度/缩放）与子任务 3 顶栏精修余项。

**来源**：用户 2026-09-30 反馈——对顶栏与左侧边栏设计不满意，
"左侧边栏展开是直接盖在整个页面上"。

**现状事实（2026-09-30 核实 `dashboard-enterprise/src/App.vue`）**：
- 布局是"双层 fixed"：顶栏全宽 fixed（z-index:10），侧栏全高 fixed（z-index:20）
  **刻意盖住顶栏左侧品牌区**（`:329-341` 注释自述"展开时盖住品牌区，rail 时露出"）；
- 内容区 `padding-left` 跟随侧栏宽度切换（rail 64px ↔ 展开 232px，`:462-463/:479`），
  **桌面端是推动布局，不会盖住内容**；
- 窗口有效宽度 <900px（`MOBILE_BREAKPOINT=900`，`:174`）切移动端 drawer：
  侧栏滑入 z-30 + 全屏遮罩 z-98，覆盖整页——移动端标准交互。

**判断**：桌面端"盖住整页"不应发生；用户实际看到的多半是
①窗口有效宽度落进 <900px（高 DPI 缩放 150% 的 1080p/1366 屏、浏览器 Ctrl+缩放
都容易触发）→ 走了移动 drawer 分支；②或指"展开盖住顶栏品牌区"的刻意设计——
观感确实差（logo 随展开消失）。①是断点/自适应问题要修，②是设计问题要改。

**子任务**：
1. **断点诊断（S）**：给 ui store 加 dev 视口读数（debug 面板或 console 一行），
   确认用户场景命中的分支；断点审查——900-1100px 区间（小笔电/半屏窗口）
   桌面 push 布局 232px 侧栏占比过重，改为该区间**默认 rail**。
2. **侧栏下移（S-M，核心改观感）**：侧栏 `top: var(--topbar-h)` 从顶栏下方开始，
   顶栏通栏品牌区常驻（不再被盖）；桌面 push 语义不变；删掉 z-index 压制的
   特意设计及相应注释。移动端 drawer 交互不变。
3. **顶栏精修（S）**：与侧栏明度阶梯（`:469-472`）一致性复核；hamburger/
   折叠按钮位置统一（现在折叠钮在侧栏头部、hamburger 在顶栏下方漂浮）。
4. **回归**：Playwright e2e 断言——1280 宽展开侧栏后内容区无遮挡
   （bounding-box 断言）、950px 宽默认 rail、<900px drawer + 遮罩行为不回归。

**验收**：用户视口场景下展开侧栏不再出现"盖住整页"；品牌区全程可见；
三条 Playwright 断言绿。

---

## 4. 总优先级与排期建议

| 优先级 | 任务 | 规模 | 仓库 | 依赖 |
|---|---|---|---|---|
| **P0** | T3.0 演化 API 哑弹排除 | S | MAOP | 无 |
| **P1** | T1.2 CRL 出厂安全化 | S | MAOS | 无 |
| **P1** | T1.1 审计链加键 | M | MAOS+MAOP(migrations) | 无 |
| **P1** | T2.2 真锁 | S-M | MAOP | 无 |
| **P1** | T2.3 flaky 时间盒 | S | MAOP | 无 |
| **P1** | T3.1 v5.2.0 收尾 | M-L | MAOP | T3.0 |
| **P2** | T2.1 dry-run 信号产出方 | M-L | MAOP | 无 |
| **P2** | T1.3 Redis 会话 | M | MAOS | 无 |
| **P2** | T1.4 时钟回拨 | S-M | MAOS | T1.1 的键派生可复用 |
| **P2** | T1.5 Cython 二进制化 | M-L | MAOS | 兼容审计 spike |
| **P3** | T1.6 在线激活 M1 | M | MAOS | T1.1/T1.5 |
| **P3** | T3.2 M2.2 spike | M | MAOP | 无（时间盒） |
| **P3** | T3.3 Operator M0 | M | MAOP | 无 |
| **P2** | T-UI 顶栏/侧栏精修 | S-M | MAOP 前端 | 无（可并行） |

**节奏建议**（对齐 CHANGELOG 发布规范）：
- 第 1 周：T3.0（随下一版 patch 发）+ T1.2 + T2.2 开工；
- 第 2-3 周：T1.1 + T3.1（含前端面板）；T2.3 时间盒；
- 第 3-5 周：T2.1（最大件）；T1.3/T1.4 穿插；
- 第 5 周起：T1.5（兼容审计 spike 通过后）、T1.6 M1、T3.2 spike、T3.3 M0 按余力排。

## 5. 通用验收纪律（每个任务都过一遍）

1. 集成测试非 mock（外部边界除外，边界要列出）；
2. 关键守卫做变异验证（改回 bug 必红）；
3. CHANGELOG 记账 + 涉及 ROADMAP 承诺的同步并入对应版本节；
4. README Known Limitations 与新语义同步（安全任务）；
5. CI 全绿（含 docs-gate）才能并；Windows 腿红过两次的教训：任何新脚本过一遍
   非 UTF-8 控制台用例口径。
