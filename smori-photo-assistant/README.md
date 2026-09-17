# SMORI 照片助手（第一阶段）

把安装照片变成 Shopify 后台的「安装案例」草稿：

1. 一次上传多张照片 → 原图原样保留，另存 2000px 压缩版（去 EXIF/GPS）和缩略图，按项目存放
2. 产品型号、分类、地点由员工填写确认（AI 不会根据照片猜）
3. Claude 看图 + 已确认信息 → 生成中英文标题、副标题、简介、项目介绍、照片说明、小红书标题正文、发布前提醒
4. 员工修改确认 → 压缩版照片上传到 Shopify Files → 创建 `installation_case` 元对象，状态 **草稿**
5. 在 Shopify 后台审核后改为「已发布」，网站自动显示（主题已支持）

没有任何密钥写在主题或代码里；都在 `.env`（不进 Git）。

## 一次性准备

### 1. Shopify 自定义应用（拿 Admin API Token）
后台 → 设置 → 应用和销售渠道 → 开发应用 → 创建应用（名字如 `SMORI Photo Assistant`）→ 配置 Admin API 权限，勾选：

- `write_metaobject_definitions`, `read_metaobject_definitions`（首次创建/补齐定义）
- `write_metaobjects`, `read_metaobjects`
- `write_files`, `read_files`
- `read_products`（可选，关联商品时用）

安装应用 → 复制 **Admin API 访问令牌**（`shpat_…`，只显示一次）。

### 2. Anthropic API Key
在 console.anthropic.com 创建 Key。

### 3. 配置
```bash
cd smori-photo-assistant
cp .env.example .env      # 填 SHOPIFY_ADMIN_TOKEN、ANTHROPIC_API_KEY、ADMIN_PASSWORD
npm install
npm run cli -- check      # 验证两边连接
npm run cli -- setup      # 创建/补齐 installation_case 元对象定义（已存在则只补缺字段）
```

`setup` 创建的定义与 `docs/smori/02-Shopify后台配置-安装案例.md` 完全一致（含 Publishable、网页、SEO 三个能力，网址前缀 `installations`）。如果已经手动建过，只会补充缺少的字段。

## 使用

### 网页界面
```bash
npm start          # http://localhost:3000  用户名 smori，密码 = ADMIN_PASSWORD
```
新建项目 → 填产品/分类/地点 → 拖入照片 → 生成文案 → 修改 → 「上传照片并创建草稿」→ 点链接到后台审核。

### 命令行（端到端测试）
```bash
npm run cli -- e2e \
  --product Silhouette --category sheer --location "Irvine, CA" \
  --room "Living Room" --date 2026-09-01 --notes "客户要求保留海景" \
  --photos ./photos/IMG_0001.jpg ./photos/IMG_0002.jpg ./photos/IMG_0003.jpg
```
参数说明：`--dry-run` 不写 Shopify，只把将要提交的内容写到 `data/projects/<id>/shopify-payload.dry-run.json`；`--skip-claude` 用占位文案（只测上传链路）。

## 数据存放
```
data/projects/<日期-随机>/
  project.json                 项目信息、照片清单、文案、Shopify 结果
  original/  01-xxxx.jpg       原图（字节不变）
  web/       01-xxxx.jpg       2000px、JPEG 82、无 EXIF —— 上传到 Shopify 的版本
  thumb/     01-xxxx.jpg       480px 缩略图
```
上传成功后每张照片记录 Shopify 文件 ID，重试不会重复上传；同一项目不会重复创建草稿。

## 已测试
- `npm test`：富文本转换、图片压缩（原图字节一致、长边 2000、EXIF 去除）、以及对本地"假 Shopify"的完整流程（自动建定义 → 3 张分段上传 → 等待 READY → 创建 DRAFT 元对象，字段与主题一致）。
- 网页界面和命令行在本地跑通（未配置密钥时正确提示，不会伪造结果）。
- **真实店铺的端到端还需要**：`SHOPIFY_ADMIN_TOKEN` + `ANTHROPIC_API_KEY` + 一组真实安装照片，然后执行上面的 `e2e` 命令。

## 安全
- Token 只在 `.env`；`.gitignore` 已排除 `.env` 和 `data/`。
- 网页界面有 Basic Auth；不要把服务直接暴露到公网，本机运行或放在内网/VPN 后。
- 上传到 Shopify 的是去掉 EXIF（含 GPS）的压缩版；原图只留在本机。
- 生成的文案中如出现人脸、门牌号等，模型会在 `warnings` 里提示，请在发布前处理。
