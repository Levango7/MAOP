# License 密钥管理

> ⚠️ **安全警告**：私钥绝不能提交到版本控制或进入 Docker 镜像。

## 当前状态

- `private_key.pem` 已被 `.gitignore` 和 `.dockerignore` 排除
- 如果此文件已存在于 Git 历史中，需要执行密钥轮换

## 密钥生成

```bash
# 生成 Ed25519 密钥对
python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

private_key = Ed25519PrivateKey.generate()

# 保存私钥
with open('private_key.pem', 'wb') as f:
    f.write(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    ))

# 保存公钥
public_key = private_key.public_key()
with open('public_key.pem', 'wb') as f:
    f.write(public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo
    ))

print('密钥对已生成: private_key.pem, public_key.pem')
"
```

## 密钥轮换流程

1. **生成新密钥对**（见上方命令）
2. **更新 License 颁发服务**使用新私钥
3. **更新所有已部署实例**的公钥
4. **重新颁发所有有效 License**
5. **清理 Git 历史**（如果旧私钥曾提交）：
   ```bash
   git filter-branch --force --index-filter \
     'git rm --cached --ignore-unmatch py/maop/enterprise/keys/private_key.pem' \
     --prune-empty --tag-name-filter cat -- --all
   ```

## 生产部署

- 私钥仅存储在 License 颁发服务上
- 应用实例只需要公钥（用于验证 License 签名）
- 使用 Vault 或 K8s Secret 管理密钥
