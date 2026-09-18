# SMORI Photo Assistant — 发布与安装（Custom distribution）

前提：代码已在 `smori-photo-assistant/`，OAuth 授权码流程、加密 token 存储、webhook、部署配置均已完成并通过测试。
以下三件事需要用**您的账号**完成（我这边没有 Fly.io、Dev Dashboard 的登录权限）：托管部署、绑定并发布应用版本、生成安装链接。

## A. 在 Shopify Dev Dashboard 里点什么

1. **创建应用**：dev.shopify.com → Dev Dashboard → **Apps** → **Create app** → 名称 `SMORI Photo Assistant` → 创建。
   记下 **Client ID** 和 **Client secret**（Settings 页）。
2. **（可选，若希望由我来部署）** Settings → **App Automation Token** → Generate → 复制令牌。把它和 Client ID / Client secret 作为环境变量交给我（不要贴在聊天里），我就能执行 `shopify app deploy`。
3. **发布版本**：终端执行 `shopify app config link` + `shopify app deploy`（或由我用自动化令牌执行）。完成后 Dev Dashboard → 应用 → **Versions** 里出现新版本并为 Active。
4. **选择分发方式**：应用主页 → **Distribution** 卡片 → **Select distribution method** → **Custom distribution** → 输入 `smori-9216.myshopify.com` → **Generate link** → 复制安装链接。
   注意：分发方式一旦选定不能更改；只对这一家店有效。
5. **安装**：用店铺 owner/staff 账号打开安装链接 → 页面显示 6 项权限 → **Install**。Shopify 会跳到 `https://smori-photo-assistant.fly.dev/auth/callback`，应用保存加密 token 后自动打开员工界面（用户 `smori`，密码为部署时设置的 `ADMIN_PASSWORD`）。

## B. 托管（Fly.io，一次）
```bash
cd smori-photo-assistant
fly launch --copy-config --no-deploy
fly volumes create data --size 3 --region lax
fly secrets set SHOPIFY_API_KEY=<ClientID> SHOPIFY_API_SECRET=<ClientSecret> \
  SESSION_SECRET=$(openssl rand -hex 32) ADMIN_PASSWORD=<密码> ANTHROPIC_API_KEY=<key>
fly deploy
```
若改用其他主机（Render、Railway 等），把 `shopify.app.toml` 里的两个 URL 和 `SHOPIFY_APP_URL` 改成新域名后再 `shopify app deploy`。

## C. 安装后验证
- 员工界面右上角显示店铺名称和「元对象定义」状态。
- 新建项目 → 上传照片 → 生成文案 → 「上传照片并创建草稿」→ 点链接到后台 **内容 → 元对象 → Installation Case**，看到状态为 **草稿** 的新条目。
- 只有在后台把状态改为「已发布」网站才会显示；应用不会自动发布。
