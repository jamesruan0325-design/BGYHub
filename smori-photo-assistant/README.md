# SMORI 照片助手（Shopify Custom Distribution 应用）

把安装照片变成 Shopify 后台的「安装案例」草稿：

1. 一次上传多张照片 → 原图原样保留，另存 2000px 压缩版（去 EXIF/GPS）和缩略图，按项目存放
2. 产品型号、分类、地点由员工填写确认（AI 不会根据照片猜）
3. Claude 看图 + 已确认信息 → 中英文标题、副标题、简介、项目介绍、照片说明、小红书标题正文、发布前提醒
4. 员工修改确认 → 压缩版照片上传到 Shopify Files → 创建 `installation_case` 元对象，状态 **草稿**
5. 在 Shopify 后台审核后手动改为「已发布」。本应用从不自动公开发布。

## 架构（本次改造）

- **Shopify CLI 管理的应用**：配置在 `shopify.app.toml`（非嵌入式、Custom distribution、OAuth 授权码流程）。
- **权限**：`read_files, write_files, read_metaobjects, write_metaobjects, read_metaobject_definitions, write_metaobject_definitions`（由 Shopify 在安装时授予，`use_legacy_install_flow = false`）。
- **OAuth**：`GET /auth?shop=…` → Shopify 授权页 → `GET /auth/callback`（校验 state + HMAC）→ 用 code 换取 **离线 access token**。
- **Token 存储**：`data/sessions.enc.json`，AES-256-GCM 加密，密钥由 `SESSION_SECRET` 派生。不在主题、不在 Git、不在日志。
- **Webhook**：`app/uninstalled` 和 3 个合规主题，校验 HMAC；卸载即删除 token。
- **员工界面**：Basic Auth（用户 `smori`，密码 `ADMIN_PASSWORD`），生产环境必须设置。
- **稳定 URL**：`https://smori-photo-assistant.fly.dev`（Fly.io，`fly.toml` + `Dockerfile`）。换主机时同时改 `shopify.app.toml` 里的 `application_url`、`redirect_urls` 和 `SHOPIFY_APP_URL`。
- 不使用 client credentials grant，不需要开发商店。

## 文件
```
shopify.app.toml   Shopify CLI 应用配置（client_id 由 `shopify app config link` 写入）
src/oauth.ts       授权码流程、HMAC 校验
src/tokens.ts      加密 token 存储
src/webhooks.ts    卸载 / 合规 webhook
src/shopify.ts     Admin GraphQL（定义、文件上传、草稿元对象）
src/copy.ts        Claude 文案生成（结构化输出）
src/images.ts      压缩流水线
src/server.ts      Express：/auth /auth/callback /webhooks/* /api/* /files/*
src/cli.ts         check / setup / e2e
public/index.html  员工界面
Dockerfile fly.toml 部署
```

## 环境变量（见 `.env.example`）
| 变量 | 说明 |
| --- | --- |
| `SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET` | Dev Dashboard 里应用的 Client ID / Client secret（`shopify app env pull` 可自动写入 `.env`） |
| `SHOPIFY_APP_URL` | 与 `shopify.app.toml` 的 `application_url` 一致 |
| `SHOPIFY_SHOP` | `smori-9216.myshopify.com` |
| `SESSION_SECRET` | `openssl rand -hex 32`，用于加密 token 和签名 cookie |
| `ADMIN_PASSWORD` | 员工界面密码 |
| `ANTHROPIC_API_KEY` | Claude |

## 部署与发布（一次性）

```bash
# 0. 本地
npm install && cp .env.example .env

# 1. 托管（Fly.io）：创建应用、持久化磁盘、密钥，然后部署
fly launch --copy-config --no-deploy          # 应用名 smori-photo-assistant，区域 lax
fly volumes create data --size 3 --region lax
fly secrets set SHOPIFY_API_KEY=… SHOPIFY_API_SECRET=… SESSION_SECRET=$(openssl rand -hex 32) ADMIN_PASSWORD=… ANTHROPIC_API_KEY=…
fly deploy
curl https://smori-photo-assistant.fly.dev/healthz    # {"ok":true}

# 2. 把本地配置和 Dev Dashboard 里的应用绑定，并发布版本
shopify app config link            # 选择 Dev Dashboard 里的 “SMORI Photo Assistant”，写入 client_id
shopify app deploy                 # 推送 URL、redirect、scopes、webhooks，创建并发布新版本
```
CI/无交互部署：在 Dev Dashboard → 应用 → Settings → **App Automation Token** 生成令牌，设 `SHOPIFY_APP_AUTOMATION_TOKEN` 后运行 `shopify app deploy --allow-updates`。

## 安装到真实店铺
Dev Dashboard → 应用主页 → **Distribution** 卡片 → **Select distribution method** → **Custom distribution** → 输入 `smori-9216.myshopify.com` → **Generate link**。店主打开该链接 → 确认权限 → Shopify 跳回 `/auth/callback` → token 加密保存 → 自动跳到员工界面。

之后：`npm run cli -- check` 应显示 `installed on "S. MORI …"`；界面右上角显示店铺名。第一次点「上传照片并创建草稿」时会自动创建/补齐 `installation_case` 元对象定义。

## 本地开发
```bash
npm start                                  # http://localhost:3000
npm run cli -- check | setup | e2e …       # 见 src/cli.ts 顶部注释
npm test                                   # 7 个离线测试（OAuth、token 加密、webhook、压缩、假 Shopify 全流程）
```

## 安全要点
- Token 只存在服务器加密文件里；`.env`、`data/` 已在 `.gitignore`。
- 所有 Shopify 回调和 webhook 都做 HMAC 校验；state 用签名 cookie 防 CSRF；shop 域名用严格正则校验。
- 上传到 Shopify 的是去 EXIF 的压缩版；原图只留在服务器磁盘。
- 应用只创建 DRAFT；发布动作只能由人在 Shopify 后台完成。
