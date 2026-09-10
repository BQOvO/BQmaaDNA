---
name: cdk-license
description: "CDK 离线授权系统：基于 RSA 非对称加密的桌面软件激活方案。包含设备指纹采集、CDK 生成/验证、LemonSqueezy 支付集成、GitHub Actions 全自动下发。Invoke when building offline license activation, CDK/keygen system, desktop software copy protection, or paid feature gating for MaaFramework-based projects."
---

# CDK 离线授权系统（桌面软件版）

基于 RSA 非对称加密的离线激活方案。核心思路：**开发者用私钥签名生成 CDK，软件内嵌公钥验证签名**。不需要服务器，不需要数据库，不需要用户注册。

---

## 一、架构概览

```
开发者电脑                      用户电脑
┌──────────────┐              ┌──────────────┐
│  私钥（绝密）  │              │  公钥（嵌入代码）│
│  CDK 生成工具  │              │  CDK 验证器    │
│              │              │  设备指纹采集   │
└──────────────┘              └──────────────┘
       │                              │
       │ ① 用户给你设备指纹              │
       │ ◄───────────────────── │
       │                              │
       │ ② 你用私钥生成 CDK            │
       │ ──────────────────────► │
       │                              │
       │                   ③ 软件用公钥验证
       │                      ├─ 签名有效？（是你签的）
       │                      ├─ 设备匹配？（绑定这台电脑）
       │                      ├─ 没过期？
       │                      └─ ✅ 激活 / ❌ 拒绝
```

---

## 二、自动化架构（推荐）

```
用户点"升级"                     GitHub（你已有的）
       │                              │
       ▼                              │
MDNA 自动:                             │
├─ 采集设备指纹                        │
├─ 生成订单号                          │
├─ 打开 LemonSqueezy 支付页            │
│  (URL 带指纹+订单号)                 │
│       │                              │
│   用户付款 ──────────────────► LemonSqueezy 收钱
│       │                              │
│       │                        webhook
│       │                              ▼
│       │                    GitHub Actions（免费）
│       │                    ├─ 取出指纹+套餐
│       │                    ├─ 用私钥生成 CDK
│       │                    └─ 写入 cdk_store.json
│       │                              │
│       │         ◄────────────────────┘
│       │
├─ 每 5 秒轮询 cdk_store.json
├─ 拿到 CDK → 本地验证 → 激活 ✅
└─ 全自动，用户只需付款
```

**需要的组件**：

| 组件 | 作用 | 费用 |
|------|------|------|
| GitHub Actions | 收到 webhook → 生成 CDK | 公开仓库免费 |
| GitHub Secrets | 存储私钥 | 免费 |
| GitHub Raw 文件 (`cdk_store.json`) | 充当"数据库" | 免费 |
| LemonSqueezy | 收钱 + 发 webhook | 免费（每笔抽 5%） |
| MDNA 内 Python 代码 | 采集指纹、轮询、激活 | 你项目里已有的 |

---

## 三、设备指纹采集

### 原理

采集 CPU、主板 UUID、硬盘序列号、Windows MachineGuid 等硬件信息，拼接后 SHA-256 取前 16 位作为短指纹。

### Python 实现（Windows）

```python
import hashlib
import subprocess

def get_device_fingerprint() -> str:
    script = r"""
        Get-CimInstance Win32_Processor | Select -Expand ProcessorID
        Write-Host "---"
        Get-CimInstance Win32_ComputerSystemProduct | Select -Expand UUID
        Write-Host "---"
        Get-CimInstance Win32_BaseBoard | Select -Expand SerialNumber
        Write-Host "---"
        Get-CimInstance Win32_DiskDrive | Where MediaType -eq 'Fixed hard disk media' | Select -Expand SerialNumber
        Write-Host "---"
        Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' | Select -Expand MachineGuid
    """
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=15
    )
    parts = [p.strip() for p in result.stdout.split("---")]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
```

### 在 MaaFramework 项目中集成

创建 `agent/custom/action/DeviceFingerprint.py`，实现 `CustomAction`：

```python
from maa.custom_action import CustomAction
from maa.context import Context

class DeviceFingerprint(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        fingerprint = get_device_fingerprint()
        # 自动复制到剪贴板
        subprocess.run(
            ["powershell", "-Command", f"Set-Clipboard -Value '{fingerprint}'"],
            timeout=5
        )
        print(f"[DeviceFingerprint] 设备指纹: {fingerprint}  （已自动复制到剪贴板！）")
        return CustomAction.RunResult(success=True)
```

在 `agent/CustomFile.py` 中注册：

```python
from agent.custom.action.DeviceFingerprint import DeviceFingerprint
_register("DeviceFingerprint", DeviceFingerprint, AgentServer.custom_action)
```

---

## 四、CDK 生成（开发者端）

### 密钥对生成

```python
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization

def generate_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    # 私钥：开发者自己保留，绝对不要泄露或提交到仓库
    with open("cdk_private_key.pem", "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # 公钥：硬编码到软件代码中
    with open("cdk_public_key.pem", "wb") as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))
```

### CDK 生成

```python
import base64
from datetime import date, timedelta

def generate_cdk(private_key, fingerprint: str, tier: str, days: int) -> str:
    expiry = date.today() + timedelta(days=days)
    expiry_str = expiry.isoformat()  # "2026-12-10"

    # 载荷：设备指纹 + 等级 + 到期日
    payload = f"{fingerprint}|{tier}|{expiry_str}".encode()

    # 用私钥签名
    signature = private_key.sign(
        payload,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                     salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256()
    )

    # CDK = base64(载荷 + 签名)
    cdk_bytes = payload + b"||" + signature
    return base64.urlsafe_b64encode(cdk_bytes).decode().rstrip("=")
```

---

## 五、CDK 验证（嵌入软件）

### 核心验证逻辑

```python
import base64
from datetime import date
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature

# 公钥硬编码在代码中（从 cdk_public_key.pem 复制内容）
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA...
-----END PUBLIC KEY-----"""


def verify_cdk(cdk: str, device_fingerprint: str) -> dict | None:
    """
    验证 CDK 是否有效。
    返回 None 表示无效；返回 dict 包含 tier 和 expiry。
    """
    try:
        # 1. 解码 CDK
        cdk += "=" * (4 - len(cdk) % 4)
        cdk_bytes = base64.urlsafe_b64decode(cdk)

        # 2. 分离载荷和签名
        payload, signature = cdk_bytes.rsplit(b"||", 1)
        bound_device, tier, expiry_str = payload.decode().split("|")

        # 3. 验证设备指纹
        if bound_device != device_fingerprint:
            return None

        # 4. 验证签名（确认是你签发的）
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode())
        public_key.verify(
            signature, payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                         salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )

        # 5. 检查过期
        if date.today() > date.fromisoformat(expiry_str):
            return None

        return {"tier": tier, "expiry": expiry_str}

    except (InvalidSignature, Exception):
        return None
```

### 激活状态持久化

```python
import json
import os
from datetime import date

ACTIVATION_FILE = "activation.json"

def save_activation(tier: str, expiry: str):
    with open(ACTIVATION_FILE, "w") as f:
        json.dump({"tier": tier, "expiry": expiry}, f)

def load_activation() -> dict | None:
    if not os.path.exists(ACTIVATION_FILE):
        return None
    with open(ACTIVATION_FILE, "r") as f:
        data = json.load(f)
    if date.today() > date.fromisoformat(data["expiry"]):
        return None
    return data
```

---

## 六、GitHub Actions 自动化（核心）

### Workflow 文件 `.github/workflows/cdk.yml`

```yaml
name: 生成 CDK

on:
  workflow_dispatch:           # 测试用：手动触发
    inputs:
      device_fingerprint:
        description: "设备指纹"
        required: true
      plan:
        description: "套餐"
        required: true
        type: choice
        options: [month, quarter, year]
  repository_dispatch:         # 生产用：LemonSqueezy webhook 触发
    types: [payment_received]

jobs:
  generate-cdk:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: 安装依赖
        run: pip install cryptography

      - name: 生成 CDK
        env:
          PRIVATE_KEY: ${{ secrets.CDK_PRIVATE_KEY }}
          DEVICE_FP: ${{ github.event.client_payload.device_fingerprint || github.event.inputs.device_fingerprint }}
          PLAN: ${{ github.event.client_payload.plan || github.event.inputs.plan }}
          ORDER_ID: ${{ github.event.client_payload.order_id || github.event.inputs.order_id || 'manual' }}
        run: python tools/generate_cdk.py

      - name: 提交 CDK 到仓库
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git add cdk_store.json
          git commit -m "CDK: $ORDER_ID" || true
          git push
```

### CDK 生成脚本 `tools/generate_cdk.py`

```python
import os
import json
import base64
from datetime import date, timedelta
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

PRIVATE_KEY = os.environ["PRIVATE_KEY"]
DEVICE_FP = os.environ["DEVICE_FP"]
PLAN = os.environ["PLAN"]
ORDER_ID = os.environ.get("ORDER_ID", "manual")

PLANS = {"month": 30, "quarter": 90, "year": 365}

def generate_cdk(fingerprint, tier, days):
    private_key = serialization.load_pem_private_key(
        PRIVATE_KEY.encode(), password=None
    )
    expiry = date.today() + timedelta(days=days)
    payload = f"{fingerprint}|{tier}|{expiry.isoformat()}".encode()
    signature = private_key.sign(
        payload,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                     salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256()
    )
    cdk_bytes = payload + b"||" + signature
    return base64.urlsafe_b64encode(cdk_bytes).decode().rstrip("=")

cdk = generate_cdk(DEVICE_FP, PLAN, PLANS.get(PLAN, 30))

# 写入 cdk_store.json
STORE_PATH = "cdk_store.json"
store = {}
if os.path.exists(STORE_PATH):
    with open(STORE_PATH, "r") as f:
        try:
            store = json.load(f)
        except json.JSONDecodeError:
            store = {}

store[ORDER_ID] = {
    "cdk": cdk,
    "device_fp": DEVICE_FP,
    "plan": PLAN,
    "expiry": (date.today() + timedelta(days=PLANS.get(PLAN, 30))).isoformat(),
}

with open(STORE_PATH, "w") as f:
    json.dump(store, f, indent=2)

print(f"CDK generated for order {ORDER_ID}")
```

### 配置 GitHub Secrets

在 GitHub 仓库 → Settings → Secrets and variables → Actions → 添加：
- `CDK_PRIVATE_KEY`: 把 `cdk_private_key.pem` 的内容粘贴进去

---

## 七、LemonSqueezy 支付集成

### 注册与配置

1. 注册 https://lemonsqueezy.com
2. 创建商品（Products）：「月度会员 ¥19」「季度会员 ¥49」「年度会员 ¥149」
3. 获取每个商品的 `variant_id` 和 API Key
4. 配置 Webhook URL：在 Settings → Webhooks → 添加
   - URL: `https://api.github.com/repos/BQOvO/MDNA/dispatches`
   - 需要创建一个 GitHub Personal Access Token (PAT) 用于认证
   - 事件选择: `order_created`

### MDNA 端全自动升级 Action

```python
# agent/custom/action/UpgradeMembership.py
import hashlib
import subprocess
import uuid
import time
import json
import urllib.request
import webbrowser
from maa.custom_action import CustomAction
from maa.context import Context

GITHUB_RAW = "https://raw.githubusercontent.com/BQOvO/MDNA/main/cdk_store.json"
PLAN = "month"  # 默认月卡

class UpgradeMembership(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        # 1. 采集设备指纹
        fingerprint = self._get_device_fingerprint()
        order_id = str(uuid.uuid4()).replace("-", "")[:16]

        print(f"[Upgrade] 设备指纹: {fingerprint}")
        print(f"[Upgrade] 订单号: {order_id}")

        # 2. 打开 LemonSqueezy 支付页（URL 带指纹和订单号作为 custom data）
        checkout_url = (
            f"https://你的店铺.lemonsqueezy.com/checkout/buy/xxx"
            f"?checkout[custom][device_fingerprint]={fingerprint}"
            f"&checkout[custom][order_id]={order_id}"
            f"&checkout[custom][plan]={PLAN}"
        )
        webbrowser.open(checkout_url)

        # 3. 轮询等 CDK
        print("[Upgrade] 等待支付完成...")
        for i in range(120):  # 最多 10 分钟
            time.sleep(5)
            try:
                resp = urllib.request.urlopen(GITHUB_RAW, timeout=10)
                store = json.loads(resp.read())
                if order_id in store:
                    cdk = store[order_id]["cdk"]
                    expiry = store[order_id]["expiry"]
                    print(f"[Upgrade] ✅ 获取到 CDK！到期: {expiry}")

                    # 自动激活
                    from agent.custom.cdk_verifier import verify_cdk, save_activation
                    result = verify_cdk(cdk, fingerprint)
                    if result:
                        save_activation(result["tier"], result["expiry"])
                        print(f"[Upgrade] ✅ 激活成功！")
                        return CustomAction.RunResult(success=True)
            except Exception:
                if i % 12 == 0:
                    print(f"[Upgrade] 等待中... ({i*5}秒)")

        print("[Upgrade] 超时")
        return CustomAction.RunResult(success=False)

    def _get_device_fingerprint(self) -> str:
        # ... 同第三节的设备指纹采集代码
```

---

## 八、测试

### 本地测试脚本 `tools/test_cdk.py`

```python
"""CDK 系统完整测试套件 — 纯本地，不需要任何外部服务"""
import sys
import os
import json
import tempfile
import base64
from datetime import date, timedelta
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature

PASS, FAIL = 0, 0

def test(name):
    def decorator(fn):
        def wrapper():
            global PASS, FAIL
            try:
                fn()
                PASS += 1
                print(f"  ✅ {name}")
            except AssertionError as e:
                FAIL += 1
                print(f"  ❌ {name}: {e}")
            except Exception as e:
                FAIL += 1
                print(f"  💥 {name}: {type(e).__name__}: {e}")
        return wrapper
    return decorator


def generate_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()

def generate_cdk(private_key, fingerprint, tier, days):
    expiry = date.today() + timedelta(days=days)
    payload = f"{fingerprint}|{tier}|{expiry.isoformat()}".encode()
    signature = private_key.sign(
        payload,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256()
    )
    return base64.urlsafe_b64encode(payload + b"||" + signature).decode().rstrip("=")

def verify_cdk(public_key, cdk, fingerprint):
    try:
        cdk += "=" * (4 - len(cdk) % 4)
        cdk_bytes = base64.urlsafe_b64decode(cdk)
        payload, signature = cdk_bytes.rsplit(b"||", 1)
        bound_fp, tier, expiry_str = payload.decode().split("|")
        if bound_fp != fingerprint:
            return False, None, "设备不匹配"
        public_key.verify(
            signature, payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256()
        )
        if date.today() > date.fromisoformat(expiry_str):
            return False, None, "已过期"
        return True, tier, expiry_str
    except InvalidSignature:
        return False, None, "签名无效"
    except Exception as e:
        return False, None, str(e)


@test("密钥对生成")
def test_keygen():
    priv, pub = generate_keys()
    assert priv.key_size == 2048

@test("有效 CDK 验证通过")
def test_valid():
    priv, pub = generate_keys()
    cdk = generate_cdk(priv, "A1B2C3D4E5F6G7H8", "gold", 30)
    ok, tier, _ = verify_cdk(pub, cdk, "A1B2C3D4E5F6G7H8")
    assert ok and tier == "gold"

@test("设备不匹配的 CDK 被拒绝")
def test_wrong_device():
    priv, pub = generate_keys()
    cdk = generate_cdk(priv, "AAAA", "gold", 30)
    ok, _, reason = verify_cdk(pub, cdk, "BBBB")
    assert not ok and "不匹配" in reason

@test("过期的 CDK 被拒绝")
def test_expired():
    priv, pub = generate_keys()
    cdk = generate_cdk(priv, "A1B2C3D4E5F6G7H8", "gold", -1)
    ok, _, reason = verify_cdk(pub, cdk, "A1B2C3D4E5F6G7H8")
    assert not ok and "过期" in reason

@test("伪造的 CDK 被拒绝")
def test_forged():
    _, pub = generate_keys()
    fake = base64.urlsafe_b64encode(b"FAKE|gold|2099-01-01||" + b"x" * 256).decode().rstrip("=")
    ok, _, _ = verify_cdk(pub, fake, "FAKE")
    assert not ok

@test("篡改的 CDK 被拒绝")
def test_tampered():
    priv, pub = generate_keys()
    cdk = generate_cdk(priv, "ORIGINAL", "gold", 30)
    original = base64.urlsafe_b64decode(cdk + "==")
    payload, signature = original.rsplit(b"||", 1)
    tampered = base64.urlsafe_b64encode(
        payload.replace(b"ORIGINAL", b"HACKEDXX") + b"||" + signature
    ).decode().rstrip("=")
    ok, _, _ = verify_cdk(pub, tampered, "HACKEDXX")
    assert not ok

@test("激活状态持久化")
def test_persistence():
    state = {"tier": "gold", "expiry": (date.today() + timedelta(days=30)).isoformat()}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(state, f)
        tmp_path = f.name
    with open(tmp_path, "r") as f:
        loaded = json.load(f)
    assert loaded["tier"] == "gold"
    os.unlink(tmp_path)


if __name__ == "__main__":
    import inspect
    print("=" * 50)
    print("  CDK 系统测试")
    print("=" * 50)
    current_module = sys.modules[__name__]
    for name, obj in inspect.getmembers(current_module):
        if callable(obj) and hasattr(obj, '__wrapped__'):
            obj()
    print("=" * 50)
    print(f"  通过: {PASS}/{PASS + FAIL}")
    if FAIL > 0:
        sys.exit(1)
```

运行：`pip install cryptography && python tools/test_cdk.py`

---

## 九、安全分析

| 攻击方式 | 能否成功 | 原因 |
|---------|:--:|------|
| 用户自己伪造 CDK | ❌ | 没有私钥，无法生成有效签名 |
| 用户把 CDK 给朋友用 | ❌ | CDK 绑定了设备指纹 |
| 用户改本地 activation.json | ⚠️ | 需配合定期重新验证 CDK 来防止 |
| 用户反编译拿到公钥 | ⚠️ | 公钥只能验证不能签名，拿到也没用 |
| 用户换硬件 | ⚠️ | 需要重新获取 CDK |

---

## 十、部署清单

一次性工作（约 1-2 小时）：

| 步骤 | 说明 | 耗时 |
|------|------|------|
| 1. 生成密钥对 | 本地运行 `generate_keys()` | 1 分钟 |
| 2. 注册 LemonSqueezy | 创建账号 + 商品 | 30 分钟 |
| 3. 配置 GitHub Secrets | 把私钥存入 `CDK_PRIVATE_KEY` | 1 分钟 |
| 4. 创建 Actions 文件 | `.github/workflows/cdk.yml` | 10 分钟 |
| 5. 创建 CDK 生成脚本 | `tools/generate_cdk.py` | 5 分钟 |
| 6. MDNA 端集成 | `DeviceFingerprint.py` + `UpgradeMembership.py` + `cdk_verifier.py` | 30 分钟 |
| 7. 配置 LemonSqueezy Webhook | 指向 GitHub Actions | 5 分钟 |
| 8. 测试 | 本地跑 `test_cdk.py` + 手动触发 Actions | 15 分钟 |

**之后全自动运行，零维护。** 你只需要偶尔登录 LemonSqueezy 看收入报表。

---

## 十一、依赖

```bash
pip install cryptography
```

---

## 十二、注意事项

1. **私钥绝对不能泄露**：私钥文件 (`cdk_private_key.pem`) 不要提交到 Git 仓库，只存在 GitHub Secrets 和本地
2. **公钥可以公开**：硬编码在代码里没关系，拿到公钥也无法伪造 CDK
3. **`cdk_store.json` 会被频繁提交**：这是正常的，GitHub Actions 每次生成 CDK 都会 push 这个文件
4. **LemonSqueezy 买家无需注册**：用户直接用支付宝/微信扫码付款即可
5. **GitHub Actions 对公开仓库免费**：每月 2000 分钟，足够用