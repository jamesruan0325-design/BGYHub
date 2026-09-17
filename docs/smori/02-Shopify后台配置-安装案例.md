# Shopify 后台配置 — 安装案例（Installation Case）

案例数据存放在 Shopify 原生的 **元对象（Metaobject）** 中，不依赖第三方 App。运营人员在后台"内容 > 元对象"里维护，支持草稿 / 发布状态，也方便后续照片助手通过 Admin API 写入。

## 1. 创建元对象定义（只做一次）

后台路径：**设置 → 自定义数据 → 元对象 → 添加定义**

| 项目 | 填写 |
| --- | --- |
| 名称 | Installation Case（显示名可以写"安装案例"） |
| 类型（Type） | `installation_case` ← 必须完全一致，主题按这个类型读取 |

### 字段（Key 必须一致，名称可自定）

| Key | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `title` | 单行文本 | 是 | 英文标题（建议设为"显示名称"字段） |
| `title_zh` | 单行文本 | | 中文标题 |
| `subtitle` | 单行文本 | | 英文副标题，如 "Silhouette in a coastal living room" |
| `subtitle_zh` | 单行文本 | | 中文副标题 |
| `category` | 单行文本 | 是 | 产品分类。验证规则选"预设选项"，填：`sheer` `blackout` `signature` `motorized` `drapery` `shutters` `commercial` |
| `product_name` | 单行文本 | | 产品名，如 Silhouette、Duette |
| `product` | 商品（Product） | | 关联 Shopify 商品（有商品时才用，详情页会显示链接） |
| `room` | 单行文本 | | 空间（英文），如 Living Room |
| `room_zh` | 单行文本 | | 空间（中文） |
| `location` | 单行文本 | | 地点，如 Irvine, CA |
| `installed_on` | 日期 | | 安装日期 |
| `summary` | 多行文本 | | 英文一句话简介（卡片和详情页顶部） |
| `summary_zh` | 多行文本 | | 中文简介 |
| `description` | 富文本 | | 英文项目介绍 |
| `description_zh` | 富文本 | | 中文项目介绍 |
| `cover` | 文件（图片） | | 封面；留空则用照片列表第一张 |
| `photos` | 文件（图片）**列表** | | 安装照片，建议 3–10 张，横图 4:3 效果最好 |
| `featured` | 布尔值 | | 勾选后可在首页"只显示精选" |
| `xhs_title` | 单行文本 | | 小红书标题（内部使用，不在网站显示，供照片助手回写） |
| `xhs_body` | 多行文本 | | 小红书正文（内部使用） |

### 功能开关（定义页右侧"选项 / Capabilities"）
- **可发布（Publishable）**：打开。条目有"草稿 / 已发布"状态，草稿不会出现在网站上 —— 这就是"审核后发布"的开关。
- **网店 / 网页（Online Store: Web pages）**：打开。每条案例会有自己的网址，详情页使用主题里的 `templates/metaobject/installation_case.json`。
- **可渲染 / SEO（Renderable）**：建议打开，可以为每条案例填写 SEO 标题和描述。

保存后，主题编辑器里"Installation Cases"区块会自动读取这些条目。

## 2. 添加一个案例

后台路径：**内容 → 元对象 → Installation Case → 添加条目**

1. 填标题、分类、产品、空间、地点、日期。
2. 上传照片到 `photos`（可多选），需要的话指定 `cover`。
3. 写英文和中文的 `summary` / `description`。
4. 状态选 **草稿** 先预览，确认后改为 **已发布**。
5. 发布后的网址在条目页右上角"查看"里可以看到。

## 3. 创建"全部案例"页面

1. **网店 → 页面 → 添加页面**，标题 `Installations`（或"安装案例"），网址句柄建议 `installations`。
2. 右侧"主题模板"选择 **installation-cases**。
3. 保存。页面地址为 `/pages/installations`，导航默认已指向这个地址；如果句柄不同，请到主题编辑器里改导航链接和详情页的"All installations"链接。

## 4. 主题编辑器里可调的设置

**首页 → Installation Cases 区块**
- 标题 / 描述 / 小标签
- 最多显示数量（默认 6）、只显示精选、是否显示分类筛选、"查看全部"按钮和链接
- Manual case 区块：后台还没建元对象时的临时方案，可上传最多 3 张图，填中英文标题和简介

**案例详情页（metaobject/installation_case）**
- "All installations" 返回链接
- 是否同时显示另一语言的介绍
- 是否显示同类案例
- 右侧 CTA 标题 / 文案 / 按钮

**导航栏**
- 3 个链接的文字和地址、按钮文字和地址

## 5. 中英文显示规则
- 访客浏览中文（zh-CN / zh-TW）版本时，优先显示 `*_zh` 字段，为空则回退到英文。
- 其他语言显示英文字段。
- 网站界面文案（"查看案例"、"项目信息"等）在 `locales/zh-CN.json`、`zh-TW.json` 中，可在"网店 → 主题 → 编辑默认主题内容"里修改。
- 如果店铺还没开启中文，请在"设置 → 语言"里添加简体中文（可先不发布）。
