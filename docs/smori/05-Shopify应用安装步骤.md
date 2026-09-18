# SMORI Photo Assistant — 发布与安装（Custom distribution）

状态（2026-09-18）：Shopify 应用已绑定并发布版本 `smori-photo-assistant-1-2`（Client ID `87e503f30877f5ab5c9b6041ed82747c`）。剩余：Fly.io 托管部署（B）、选择 Custom distribution 并生成安装链接（A-4）、安装（A-5）。

## A. 在 Shopify Dev Dashboard 里点什么

1. **创建应用**：dev.shopify.com → Dev Dashboard → **Apps** → **Create app** → 名称 `SMORI Photo Assistant` → 创建。
   记下 **Client ID** 和 **Client secret**（Settings 页）。
2. **（可选，若希望由我来部署）** Settings → **App Automation Token** → Generate → 复制令牌。把它和 Client ID / Client secret 作为环境变量交给我（不要贴在聊天里），我就能执行 `shopify app deploy`。
3. **发布版本**：终端执行 `shopify app config link` + `shopify app deploy`（或由我用自动化令牌执行）。完成后 Dev Dashboard → 应用 → **Versions** 里出现新版本并为 Active。
4. **选择分发方式**：应用主页 → **Distribution** 卡片 → **Select distribution method** → **Custom distribution** → 输入 `smori-9216.myshopify.com` → **Generate link** → 复制安装链接。
   注意：分发方式一旦选定不能更改；只对这一家店有效。
5. **安装**：用店铺 owner/staff 账号打开安装链接 → 页面显示 6 项权限 → **Install**。Shopify 会跳到 `https://smori-photo-assistant.fly.dev/auth/callback`，应用保存加密 token 后自动打开员工界面（用户 `smori`，密码为部署时设置的 `ADMIN_PASSWORD`）。

## B. 托管（Fly.io，在您自己的电脑上执行，密钥只在 Fly Dashboard 里添加）

前提：电脑上已安装 Git 并克隆了仓库分支 `claude/optimistic-volta-u7k87g`。

```bash
# 1. 安装 Fly CLI 并用浏览器登录（macOS / Linux）
curl -L https://fly.io/install.sh | sh
fly auth login

# 2. 进入应用目录，创建 Fly 应用（先不部署）
cd BGYHub/smori-photo-assistant
fly launch --copy-config --no-deploy --name smori-photo-assistant --region lax
#   若提示名称已被占用，换一个名称，并把新名称告诉我（Shopify 里的 URL 需要同步改）

# 3. 创建持久化磁盘（存原图、压缩图和加密 token）
fly volumes create data --size 3 --region lax --yes
```

**4. 在 Fly Dashboard 添加 Secrets**（fly.io → 应用 `smori-photo-assistant` → 左侧 **Secrets** → **New secret**），逐个添加：

| 名称 | 值 |
| --- | --- |
| `SHOPIFY_API_KEY` | `87e503f30877f5ab5c9b6041ed82747c`（Client ID，公开） |
| `SHOPIFY_API_SECRET` | Dev Dashboard → 应用 → Settings → Client secret |
| `SESSION_SECRET` | 本机运行 `openssl rand -hex 32` 生成的 64 位随机串 |
| `ADMIN_PASSWORD` | 员工界面密码（用户名固定为 `smori`） |
| `ANTHROPIC_API_KEY` | Anthropic Console 创建的 Key（服务器生成文案时直接调用 Anthropic，必须放在这里） |

其余非敏感变量（`SHOPIFY_APP_URL`、`SHOPIFY_SHOP`、`DATA_DIR` 等）已写在 `fly.toml` 的 `[env]` 里。

```bash
# 5. 部署并检查
fly deploy
curl https://smori-photo-assistant.fly.dev/healthz     # 应返回 {"ok":true}
```

若改用其他主机（Render、Railway 等），把 `shopify.app.toml` 里的两个 URL 和 `fly.toml` 的 `SHOPIFY_APP_URL` 改成新域名后告诉我，我重新发布 Shopify 版本。

## C. 安装后验证
- 员工界面右上角显示店铺名称和「元对象定义」状态。
- 新建项目 → 上传照片 → 生成文案 → 「上传照片并创建草稿」→ 点链接到后台 **内容 → 元对象 → Installation Case**，看到状态为 **草稿** 的新条目。
- 只有在后台把状态改为「已发布」网站才会显示；应用不会自动发布。
