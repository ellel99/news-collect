# Provider Credentials Private Template

复制本文件内容到仓库根目录 `PROVIDER_CREDENTIALS_PRIVATE.md`，只在本地填写。该私有文件已
加入 `.gitignore`，但真实 key 仍应优先存入系统 secret manager 或未跟踪的 `.env`。

禁止把填过的副本加入 Git、PR、日志、截图或聊天。

## NewsAPI.ai / Event Registry

```text
注册邮箱：
API key：
plan 名称：
quota / token limit：
allowed retention：
是否允许 internal AI analysis：
是否允许保存 title：
是否允许保存 summary：
是否允许保存 url：
是否允许保存 metadata：
是否允许生产环境使用：
备注：
```

## Marketaux

```text
注册邮箱：
API token：
plan 名称：
daily limit：
monthly limit：
单次 articles 上限：
allowed retention：
是否允许 internal AI analysis：
备注：
```

## Finnhub

```text
注册邮箱：
API key：
plan 名称：
rate limit：
Company News 是否可用：
Quote 是否可用：
Candles 是否可用：
Financials 是否可用：
allowed retention：
备注：
```

## EIA Open Data

```text
注册邮箱：
API key：
API version：v2
usage / rate limit：
备注：
```

## SEC EDGAR

```text
API key：不需要
User-Agent：
contact email：
访问频率要求：
备注：
```

`SEC_USER_AGENT` 应是可识别的应用/组织名称，`SEC_CONTACT_EMAIL` 应是有效联系人；二者用于
遵守 SEC automated-access 要求，不是 API key。

将确认后的值映射到 `.env.example` 中同名环境变量；不要把本模板本身当成 `.env` 解析。
