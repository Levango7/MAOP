# PyPI 上传指南

> 本文档供未来拥有 PyPI 账号时使用。
> 当前状态：**未上传**（无 PyPI 账号）。
>
> **2026-10-06 更正两处旧断言**（原文件写于"本机没有梯子"的时期，现在不成立了）：
> ① **不需要梯子**：本机实测 `pypi.org` TCP 0.22s / `https://pypi.org/` HTTP 200、
> `upload.pypi.org` TCP 0.35s、`test.pypi.org` TCP 0.37s、`docs.pypi.org` 可抓正文。
> ② **没有收费项**：公开包的注册与上传不花钱；PyPI 官方 FAQ 原文是
> "PyPI does not support publishing private packages. If you need to publish your private
> package to a package index, the recommended solution is to run your own deployment of the
> devpi project."（出处：https://pypi.org/help/ ，2026-10-06 抓取）——
> 也就是说"要花钱/要自建"的场景只出现在**想要私有索引**时；公开包不涉及费用。
>
> **不想注册账号也有可用的分发路径**：`ci.yml` 的 `Release Assets` 作业在打 tag 时把
> wheel + sdist 挂到 GitHub Release（runner 自带 token，零第三方服务）。使用者：
>
> ```bash
> pip install https://github.com/Levango7/MAOP/releases/download/v5.2.1/maop_orchestrator-5.2.1-py3-none-any.whl
> ```
>
> 这条路不需要 PyPI 账号，也不引入长期凭据。下面 §2 起是"要正式上 PyPI"时的步骤。

## 0. 两条路怎么选

| | GitHub Release 附件（已上线） | PyPI |
|---|---|---|
| 前置 | 无 | PyPI 账号（免费）+ 项目配置 |
| 安装体验 | 完整 URL / `pip install git+…` | `pip install maop-orchestrator` |
| 版本解析、依赖自动升级 | 无（PyPI 的 resolver 不认识它） | 有 |
| 谁能做 | CI 自动 | 需要人注册一次账号 |

结论：**先用 Release 附件把"拿不到包"解决掉**；等要做正式对外发布时再上 PyPI，
两条路可以并存（CI 里是两个互不依赖的作业）。

> **这条路已经端到端验证过**（2026-10-06）：在 `v5.2.0` 的 tag 提交（`665e3524`）上
> `python -m build` 出 wheel（1,241,526 B）+ sdist（1,749,318 B）→
> `scripts/attach_release_assets.py` 上传并**回读 release assets 校验**通过 →
> `pip install --no-deps <release-asset-url>` 成功，装出来的包
> `maop.__version__ == "5.2.0"` 且 `python -m maop --help` 正常。
> 注意两点口径：① 附件必须**从对应 tag 构建**，否则会出现"版本名与实际内容不符"的发行物；
> ② `v5.2.0` 这个 release 原本是**空附件**的（实测 `assets: []`），是这次补挂的。

## 1. 背景

PyPI 上 `maop` 包名已被他人（Justin Ryan, 2021-07-09）注册为 0.0.0 版本（简单计算器包）。
因此个人版包名改为 `maop-orchestrator`，import 名仍为 `maop`（不变）。
2026-10-06 实测 `https://pypi.org/pypi/maop-orchestrator/json` 返回 **404** ⇒ 这个名字还没被占。

## 1.5 推荐方式：Trusted Publishing（免长期 token）

官方步骤（出处：https://docs.pypi.org/trusted-publishers/adding-a-publisher/ ）：
Your projects → 项目 **Manage** → 侧栏 **Publishing** → 选 **GitHub Actions** 标签，
填 **Owner / Repository / Workflow filename**（本仓库对应 `Levango7` / `MAOP` / `ci.yml`）。

`ci.yml` 的 publish 作业已经用 OIDC（`permissions: id-token: write`，无 token secret），
所以这条最省事：**不用生成、也不用保管任何长期凭据**。

**项目还不存在也能一次搞定**：官方支持 "pending" publisher ——
> "you can also use them to create a PyPI project! … configure a 'pending' publisher that
> will create the project when used for the first time. 'Pending' publishers are converted
> into 'normal' publishers on first use"（出处：https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/ ）

即在**账号侧**（不是项目侧）配一个 pending publisher，第一次打 tag 发布时
`maop-orchestrator` 会被自动创建。


## 2. 前置条件

1. **PyPI 账号**：在 https://pypi.org/account/register/ 注册
2. **API Token**：在 https://pypi.org/manage/account/token/ 创建 API token（scope: Entire account 或指定项目）
3. **网络**：需能访问 `upload.pypi.org` —— 2026-10-06 本机实测**直连可达**（TCP 0.35s），无需梯子
4. **twine**：已安装（`pip install twine`，当前版本 7.0.0）

## 3. 上传步骤

### 3.1 配置 PyPI 认证

在用户主目录创建或编辑 `~/.pypirc`（Windows: `C:\Users\<用户名>\.pypirc`）：

```ini
[pypi]
username = __token__
password = pypi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> **先看 §1.5**：Trusted Publishing 不需要任何长期凭据，优先用它。
> **安全提示**：`.pypirc` 含敏感凭证，确保文件权限仅限当前用户。
> 不要将此文件提交到 Git。

### 3.2 验证产物

```powershell
# 检查 wheel 和 sdist 存在
ls F:\Nexus\MAOP\py\dist\

# 预期输出：
# maop_orchestrator-5.1.0-py3-none-any.whl
# maop_orchestrator-5.1.0.tar.gz

# 检查 wheel METADATA
python -c "import zipfile; z=zipfile.ZipFile(r'F:\Nexus\MAOP\py\dist\maop_orchestrator-5.1.0-py3-none-any.whl'); meta=[n for n in z.namelist() if n.endswith('METADATA')]; print(z.read(meta[0]).decode()[:500])"
```

### 3.3 上传

```powershell
# 本机 2026-10-06 实测可直连；若日后网络变化再设代理
# $env:HTTPS_PROXY = "http://127.0.0.1:7890"
# $env:HTTP_PROXY = "http://127.0.0.1:7890"

# 上传
python -m twine upload F:\Nexus\MAOP\py\dist\maop_orchestrator-5.1.0-py3-none-any.whl F:\Nexus\MAOP\py\dist\maop_orchestrator-5.1.0.tar.gz
```

### 3.4 验证上传

```powershell
# 检查 PyPI 上包是否存在
python -c "import urllib.request, json; req=urllib.request.Request('https://pypi.org/pypi/maop-orchestrator/json', headers={'User-Agent':'check/1.0'}); data=json.loads(urllib.request.urlopen(req).read()); print('Version:', data['info']['version'])"

# 在干净 venv 中测试安装
python -m venv F:\temp\pypi_test
F:\temp\pypi_test\Scripts\Activate.ps1
pip install maop-orchestrator
python -c "import maop; print(maop.__version__)"
# 预期输出: 5.1.0
deactivate
```

## 4. 后续版本上传

未来发布新版本时：

1. 更新 `py/maop/__init__.py` 中 `__version__`
2. 更新 `py/pyproject.toml` 中 `version`
3. 清理并重新 build：`python -m build`（在 `py/` 目录下）
4. 上传：`python -m twine upload dist/*`

## 5. 企业版上传（可选）

企业版 `maop-enterprise` 包在 `F:\Nexus\MAOS` 仓库：
- wheel：`F:\Nexus\MAOS\dist\maop_enterprise-5.1.0-py3-none-any.whl`
- 企业版可上传到私有 PyPI 或 GitHub Release（推荐后者，保持私有性）

## 6. 注意事项

- PyPI 不允许重复上传同一版本，上传前确认版本号正确
- 首次上传后，包名 `maop-orchestrator` 即被占用，后续版本可直接上传
- 如上传超时，先重试（同一版本重复上传会被拒：`Filename or contents already exists`），
  再考虑代理 —— 2026-10-06 实测本机不需要代理
- sdist（tar.gz）和 wheel 都建议上传，sdist 供源码编译，wheel 供直接安装