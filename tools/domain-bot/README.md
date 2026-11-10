# 便宜域名机器人（Porkbun）

在你给出的候选名字里找出最便宜且可注册的域名，并在你明确确认后通过
[Porkbun 官方 API](https://porkbun.com/api/json/v3/documentation) 自动注册。

- 只需要 Python 3，无第三方依赖。
- **默认是试算**：不加 `--confirm` 不会花一分钱。
- 有单价上限、总预算上限、单次最多 10 个域名三道保险。

## 准备

1. 注册 [Porkbun](https://porkbun.com) 账号，验证邮箱和手机号。
2. 在 <https://porkbun.com/account/credit> 充值余额（API 注册从余额扣款，只绑卡不够）。
3. 在 <https://porkbun.com/account/api> 创建 API key，并设置环境变量：

```bash
export PORKBUN_API_KEY=pk1_xxx
export PORKBUN_SECRET_KEY=sk1_xxx
```

想先免费演练，可以在同一页面创建**沙盒 key**（`pk1_sb_` 开头）：所有操作都是模拟的，不会真的注册或扣款。

密钥不要写进代码或提交到仓库。

## 用法

看哪些后缀最便宜（不需要 key）：

```bash
python3 domain_bot.py prices --years 3 --max-renewal 5
```

查候选名字的可用性和价格（关键词会自动拼上各个后缀，也可以直接写完整域名）：

```bash
python3 domain_bot.py check myname another-name myname.dev --tlds xyz,site,com
```

试算：看机器人会买哪个、花多少钱（不会真的购买）：

```bash
python3 domain_bot.py buy myname --tlds xyz,site,com --max-price 3 --max-renewal 20
```

确认购买：

```bash
python3 domain_bot.py buy myname --tlds xyz,site,com --max-price 3 --no-auto-renew --confirm --agree-to-terms
```

## 全自动：随便买一个最便宜的，并接入 Cloudflare

不写名字时，机器人会自己生成随机名字，并在当前最便宜的几个后缀上尝试：

```bash
python3 domain_bot.py buy --cloudflare --no-auto-renew
```

确认无误后加上 `--confirm --agree-to-terms` 真的购买。

`--cloudflare` 用的是 Porkbun 自带的 Cloudflare 对接：注册成功后，Porkbun 会在**你自己的 Cloudflare 账号**里创建站点、复制 DNS 记录，并把域名服务器改到 Cloudflare。使用前需要做一次授权：

1. 先注册一个 Cloudflare 账号（免费）。
2. 浏览器打开 <https://porkbun.com/account/connectCloudflare>，按提示授权。这一步只能人工在浏览器里完成。

没授权时机器人会给出授权链接并在购买前停下，不会扣钱。接入是后台进行的，通常几分钟，最长可能要 72 小时：

```bash
python3 domain_bot.py cloudflare status example.xyz
```

已经买好的域名也可以单独接入：

```bash
python3 domain_bot.py cloudflare connect example.xyz
```

## `buy` 的参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--tlds` | `xyz,site,online,…,com` | 关键词要尝试的后缀 |
| `--count` | 1 | 本次注册几个（最多 10） |
| `--max-price` | 3 | 单个域名首年价格上限（美元） |
| `--max-renewal` | 不限 | 续费价高于此值的域名跳过 |
| `--budget` | 5 | 本次总花费上限（美元） |
| `--years` | 1 | 按 N 年总成本排序（首年价 + 续费价 ×(N-1)） |
| `--exclude-tlds` | `top` | 随机取名时不选的后缀 |
| `--cloudflare` | 关 | 注册后把域名接入你的 Cloudflare 账号 |
| `--no-auto-renew` | 关 | 注册后关闭自动续费，避免第二年被按高价续费 |
| `--confirm` | 关 | 真的购买；不加就是试算 |
| `--agree-to-terms` | 关 | 表示你同意 [Porkbun 的注册协议和条款](https://porkbun.com/legal)，`--confirm` 时必填 |
| `--log` | `purchases.jsonl` | 购买记录文件 |

## 注意事项

- **看续费价**：很多后缀首年 1–2 美元，续费却要 15–30 美元。用 `--max-renewal`、`--years` 或 `--no-auto-renew` 控制。
- **溢价域名买不了**：Porkbun 的 API 不能注册标记为 premium 的域名（例如约 1 美元的纯数字 `.xyz`），机器人会自动跳过，这类域名需要在网页上购买。
- **Handshake 后缀会被排除**：Porkbun 也卖 `.vvip`、`.c` 这类不属于正常 DNS 的后缀，很便宜但普通浏览器和邮件服务器无法解析，机器人不会列出也不会购买。
- **Cloudflare 接入无法在沙盒里演练**：沙盒 key 会跳过这一步。
- **最便宜的后缀名声较差**：`.bond`、`.cfd`、`.cyou`、`.sbs` 首年约 1.28 美元，但被垃圾邮件滥用较多，用它们对外发信容易进垃圾箱。
- **账号侧限额**：Porkbun 对 API 默认有每月 100 美元的消费上限，可在 API 设置页调整。
- **不要大量扫描**：只查不买的大批量查询会被 Porkbun 限流 24 小时。
- 注册为注册局规定的最短年限（通常 1 年），WHOIS 隐私默认开启。
