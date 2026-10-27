# 天气查询

Miyu 脚本包，带一件工具 `weather`：查实时天气和预报，也能查空气质量、历史天气、海况、气候趋势和海拔。数据来自 [Open-Meteo](https://open-meteo.com)（免密钥），实时预报失败时退到 [wttr.in](https://wttr.in)。

## 参数

| 参数 | 说明 |
|---|---|
| `location` | 必写。城市、地名或机场代码。国外的地方用英文或当地文字（Tokyo、東京）更准，常见的中文名（东京、纽约……）脚本会自己换 |
| `query` | `forecast`（默认，实时+逐日预报）、`air_quality`、`historical`、`marine`、`climate`、`elevation` |
| `days` | 预报天数 1–16，默认 3（空气质量、海况至多 7） |
| `start_date`、`end_date` | `historical`（1940 年起，到两天前）、`climate`（到 2050 年）要写；一次至多 31 天 |
| `country_code` | 两位国家代码，同名地点里挑对的那个 |
| `format` | `md`（默认）或 `json`（给 `miyu call` 的脚本用） |

## 注意

- 选中的地点不一定对：输出末尾列出同名的其他地点；选中的是没有人口数据的小地方时会带一条警告。
- 只用 Python 标准库。地名缓存 30 天，天气缓存 10 分钟，都放在 `MIYU_SCRIPT_CACHE_DIR`。
- 空气质量、历史、海况、气候都是模型数据，输出里写了出处，转述时别当成实测。
