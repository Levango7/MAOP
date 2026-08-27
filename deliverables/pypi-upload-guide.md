# PyPI 上传指南

> 本文档供未来拥有 PyPI 账号和网络条件（梯子）时使用。
> 当前状态：**未上传**（无 PyPI 账号 + 无梯子）。

## 1. 背景

PyPI 上 `maop` 包名已被他人（Justin Ryan, 2021-07-09）注册为 0.0.0 版本（简单计算器包）。
因此个人版包名改为 `maop-orchestrator`，import 名仍为 `maop`（不变）。

## 2. 前置条件

1. **PyPI 账号**：在 https://pypi.org/account/register/ 注册
2. **API Token**：在 https://pypi.org/manage/account/token/ 创建 API token（scope: Entire account 或指定项目）
3. **网络**：需能访问 `upload.pypi.org`（可能需要梯子）
4. **twine**：已安装（`pip install twine`，当前版本 7.0.0）

## 3. 上传步骤

### 3.1 配置 PyPI 认证

在用户主目录创建或编辑 `~/.pypirc`（Windows: `C:\Users\<用户名>\.pypirc`）：

```ini
[pypi]
username = __token__
password = pypi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

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
# 设置梯子（如需要）
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
- 如上传超时，检查网络/梯子，重试即可（不会产生重复）
- sdist（tar.gz）和 wheel 都建议上传，sdist 供源码编译，wheel 供直接安装