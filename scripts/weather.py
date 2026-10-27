#!/usr/bin/env python3
# Display name: Weather
# 显示名称：天气查询
# Description: Weather for a place: current conditions and forecast, or air quality, past weather, marine, climate trend and elevation. Pass places outside China by their English or local name, and ask the user when the place is unclear instead of guessing.
# 描述：查天气：实时与预报，也能查空气质量、历史天气、海况、气候趋势和海拔。
# Timeout: 60
# Clicall: yes
# Parameters:
# {"type": "object", "properties": {
#   "location": {"type": "string", "description": "City, place name or airport code. Places outside China work best in English or the local language (Tokyo, 東京), not in Chinese."},
#   "query": {"type": "string", "enum": ["forecast", "air_quality", "historical", "marine", "climate", "elevation"], "description": "forecast (default): now plus daily forecast. historical and climate need start_date."},
#   "days": {"type": "integer", "description": "Forecast days for forecast, air_quality and marine, 1-16. Default 3."},
#   "start_date": {"type": "string", "description": "YYYY-MM-DD, for historical (from 1940) and climate (up to 2050)."},
#   "end_date": {"type": "string", "description": "YYYY-MM-DD. Default start_date. At most 31 days after start_date."},
#   "country_code": {"type": "string", "description": "ISO 3166-1 alpha-2 code such as JP, to pick the right place among same-named ones."},
#   "format": {"type": "string", "enum": ["md", "json"], "description": "md (default) for reading; json for code, e.g. piping miyu call into jq or python."}
# }, "required": ["location"]}
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
CLIMATE_URL = "https://climate-api.open-meteo.com/v1/climate"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
WTTR_URL = "https://wttr.in/"

USER_AGENT = "miyu-scripts-weather/0.1"
HTTP_TIMEOUT = 15
GEOCODING_TTL = 30 * 24 * 3600
WEATHER_TTL = 10 * 60
MAX_RANGE_DAYS = 31

QUERIES = ("forecast", "air_quality", "historical", "marine", "climate", "elevation")

# Open-Meteo 的地名库对外国城市的中文名只认得同名的小村子（「东京」会落到江苏），常见的先换成本地名。
ALIASES = {
    "东京": "Tokyo", "東京": "Tokyo", "东京都": "Tokyo", "大阪": "Osaka", "京都": "Kyoto",
    "名古屋": "Nagoya", "横滨": "Yokohama", "神户": "Kobe", "札幌": "Sapporo", "福冈": "Fukuoka",
    "冲绳": "Okinawa", "那霸": "Naha", "奈良": "Nara", "首尔": "Seoul", "汉城": "Seoul",
    "釜山": "Busan", "纽约": "New York", "伦敦": "London", "巴黎": "Paris", "洛杉矶": "Los Angeles",
    "旧金山": "San Francisco", "西雅图": "Seattle", "芝加哥": "Chicago", "华盛顿": "Washington",
    "多伦多": "Toronto", "温哥华": "Vancouver", "莫斯科": "Moscow", "柏林": "Berlin",
    "慕尼黑": "Munich", "罗马": "Rome", "米兰": "Milan", "马德里": "Madrid", "巴塞罗那": "Barcelona",
    "阿姆斯特丹": "Amsterdam", "维也纳": "Vienna", "苏黎世": "Zurich", "曼谷": "Bangkok",
    "清迈": "Chiang Mai", "普吉岛": "Phuket", "新加坡": "Singapore", "吉隆坡": "Kuala Lumpur",
    "河内": "Hanoi", "胡志明市": "Ho Chi Minh City", "雅加达": "Jakarta", "巴厘岛": "Bali",
    "马尼拉": "Manila", "迪拜": "Dubai", "悉尼": "Sydney", "墨尔本": "Melbourne",
    "奥克兰": "Auckland", "开罗": "Cairo", "伊斯坦布尔": "Istanbul", "新德里": "New Delhi",
    "孟买": "Mumbai",
}

WEATHER_CODES = {
    0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴", 45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "小冻毛毛雨", 57: "大冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "小冻雨", 67: "大冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "小阵雪", 86: "大阵雪",
    95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴大冰雹",
}
DIRECTIONS = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
FEATURE_SCORE = {"PPLC": 80_000, "PPLA": 60_000, "PPLA2": 50_000, "PPLA3": 40_000,
                 "PPLA4": 30_000, "AIRP": 25_000, "PPL": 20_000}


class Failed(Exception):
    pass


# ---------------------------------------------------------------- 缓存、请求

def cache_get(kind, key, ttl):
    root = os.environ.get("MIYU_SCRIPT_CACHE_DIR")
    if not root:
        return None
    path = os.path.join(root, kind, hashlib.sha1(key.encode()).hexdigest() + ".json")
    try:
        if time.time() - os.path.getmtime(path) < ttl:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return None


def cache_put(kind, key, value):
    root = os.environ.get("MIYU_SCRIPT_CACHE_DIR")
    if not root:
        return
    folder = os.path.join(root, kind)
    path = os.path.join(folder, hashlib.sha1(key.encode()).hexdigest() + ".json")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def http_get(url, params=None, as_json=True):
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode()).get("reason") or ""
        except (ValueError, OSError, AttributeError):
            pass
        raise Failed(f"{urllib.parse.urlparse(url).netloc} 返回 HTTP {exc.code}" + (f"：{detail}" if detail else ""))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Failed(f"连不上 {urllib.parse.urlparse(url).netloc}：{getattr(exc, 'reason', exc)}")
    if not as_json:
        return body
    try:
        return json.loads(body)
    except ValueError:
        raise Failed(f"{urllib.parse.urlparse(url).netloc} 返回的不是 JSON")


def cached_get(url, params, ttl=WEATHER_TTL):
    key = url + "?" + urllib.parse.urlencode(params)
    hit = cache_get("weather", key, ttl)
    if hit is not None:
        return hit
    data = http_get(url, params)
    cache_put("weather", key, data)
    return data


# ---------------------------------------------------------------- 地名

def squash(text):
    return "".join(text.lower().split())


def geocode(location, country):
    key = f"{squash(location)}|{country or ''}"
    hit = cache_get("geocoding", key, GEOCODING_TTL)
    if hit is not None:
        return hit
    names = [location]
    alias = ALIASES.get(squash(location))
    if alias:
        names.insert(0, alias)
    results, seen = [], set()
    for name in names:
        params = {"name": name, "count": 10, "language": "zh", "format": "json"}
        if country:
            params["countryCode"] = country
        for item in http_get(GEOCODING_URL, params).get("results") or []:
            if not all(k in item for k in ("name", "latitude", "longitude")):
                continue
            ident = (round(item["latitude"], 3), round(item["longitude"], 3))
            if ident not in seen:
                seen.add(ident)
                item["_alias"] = name != location
                results.append(item)
    cache_put("geocoding", key, results)
    return results


def score(query, item):
    q, n = squash(query), squash(item["name"])
    value = 50_000 if q == n else 20_000 if (q in n or n in q) else 0
    if item.get("_alias"):
        value += 2_000_000
    value += FEATURE_SCORE.get(item.get("feature_code"), 0)
    return value + min(int(item.get("population") or 0), 10_000_000)


def place_name(item):
    parts = []
    for part in (item.get("name"), item.get("admin1"), item.get("country")):
        if part and part not in parts:
            parts.append(part)
    return "，".join(parts)


def resolve(request):
    found = geocode(request["location"], request["country_code"])
    if not found:
        raise Failed(f"找不到地点「{request['location']}」：换个写法（英文名、本地名、加上省或州），或者传 country_code")
    ranked = sorted(found, key=lambda item: score(request["location"], item), reverse=True)
    place = ranked[0]
    info = {
        "name": place_name(place),
        "latitude": place["latitude"], "longitude": place["longitude"],
        "country_code": place.get("country_code"), "timezone": place.get("timezone"),
        "elevation_m": place.get("elevation"), "population": place.get("population"),
    }
    others = []
    for item in ranked[1:]:
        name = place_name(item)
        if name != info["name"] and name not in others:
            others.append(name)
    others = others[:3]
    warning = None
    if not place.get("population") and place.get("feature_code") not in FEATURE_SCORE.keys() - {"PPL"}:
        warning = "选中的是一个没有人口数据的小地方，可能不是要查的那个；外国地名请改用英文或本地名重查"
    return info, others, warning


# ---------------------------------------------------------------- 格式

def weather_label(code):
    return WEATHER_CODES.get(int(code), f"天气代码 {code}") if code is not None else "未知"


def direction(degrees):
    return DIRECTIONS[int((float(degrees) % 360 + 22.5) // 45) % 8] if degrees is not None else "?"


def num(value, unit="", digits=1):
    if value is None:
        return "?"
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:.0f}"
    return text + unit


def column(block, name, i):
    values = block.get(name) or []
    return values[i] if i < len(values) else None


def eu_aqi(v):
    return None if v is None else "良好" if v <= 20 else "尚可" if v <= 40 else "中等" if v <= 60 else "差" if v <= 80 else "很差" if v <= 100 else "极差"


def us_aqi(v):
    return None if v is None else "良好" if v <= 50 else "中等" if v <= 100 else "对敏感人群不健康" if v <= 150 else "不健康" if v <= 200 else "很不健康" if v <= 300 else "危险"


def coords(place):
    return {"latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto"}


# ---------------------------------------------------------------- 各种查询

def q_forecast(place, request):
    data = cached_get(FORECAST_URL, {
        **coords(place),
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,"
                   "cloud_cover,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max,wind_speed_10m_max,uv_index_max,sunrise,sunset",
        "forecast_days": request["days"],
    })
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    days = []
    for i, date in enumerate(daily.get("time") or []):
        days.append({
            "date": date, "weather": weather_label(column(daily, "weather_code", i)),
            "min_c": column(daily, "temperature_2m_min", i), "max_c": column(daily, "temperature_2m_max", i),
            "precip_mm": column(daily, "precipitation_sum", i),
            "precip_prob": column(daily, "precipitation_probability_max", i),
            "wind_max_kmh": column(daily, "wind_speed_10m_max", i),
            "uv_max": column(daily, "uv_index_max", i),
            "sunrise": (column(daily, "sunrise", i) or "")[11:], "sunset": (column(daily, "sunset", i) or "")[11:],
        })
    current = {
        "time": cur.get("time"), "weather": weather_label(cur.get("weather_code")),
        "temp_c": cur.get("temperature_2m"), "feels_c": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"), "precip_mm": cur.get("precipitation"),
        "cloud": cur.get("cloud_cover"), "wind_kmh": cur.get("wind_speed_10m"),
        "wind_dir": direction(cur.get("wind_direction_10m")), "gust_kmh": cur.get("wind_gusts_10m"),
    }
    lines = [
        f"**现在**（{(current['time'] or '').replace('T', ' ')} 当地）：{current['weather']} {num(current['temp_c'], '°C')}，"
        f"体感 {num(current['feels_c'], '°C')}，湿度 {num(current['humidity'], '%', 0)}，"
        f"{current['wind_dir']}风 {num(current['wind_kmh'], ' km/h')}（阵风 {num(current['gust_kmh'], ' km/h')}）",
        "",
        "| 日期 | 天气 | 气温 | 降水 | 降水概率 | 最大风速 | 紫外线 | 日出/日落 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in days:
        lines.append(f"| {d['date']} | {d['weather']} | {num(d['min_c'])}~{num(d['max_c'], '°C')} | "
                     f"{num(d['precip_mm'], ' mm')} | {num(d['precip_prob'], '%', 0)} | {num(d['wind_max_kmh'], ' km/h')} | "
                     f"{num(d['uv_max'])} | {d['sunrise']}/{d['sunset']} |")
    return {"current": current, "daily": days}, lines, None


def q_air_quality(place, request):
    data = cached_get(AIR_QUALITY_URL, {
        **coords(place),
        "current": "european_aqi,us_aqi,pm2_5,pm10,ozone,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide,uv_index",
        "hourly": "us_aqi,pm2_5", "forecast_days": min(request["days"], 7),
    })
    cur = data.get("current") or {}
    current = {
        "time": cur.get("time"), "eu_aqi": cur.get("european_aqi"), "eu_level": eu_aqi(cur.get("european_aqi")),
        "us_aqi": cur.get("us_aqi"), "us_level": us_aqi(cur.get("us_aqi")),
        "pm2_5": cur.get("pm2_5"), "pm10": cur.get("pm10"), "o3": cur.get("ozone"),
        "no2": cur.get("nitrogen_dioxide"), "so2": cur.get("sulphur_dioxide"), "co": cur.get("carbon_monoxide"),
        "uv": cur.get("uv_index"),
    }
    hourly = data.get("hourly") or {}
    by_day = {}
    for i, stamp in enumerate(hourly.get("time") or []):
        value = column(hourly, "us_aqi", i)
        if value is not None:
            by_day.setdefault(stamp[:10], []).append(value)
    days = [{"date": d, "us_aqi_max": max(v), "us_level": us_aqi(max(v))} for d, v in sorted(by_day.items())]
    lines = [
        f"**现在**（{(current['time'] or '').replace('T', ' ')} 当地）：US AQI {num(current['us_aqi'], digits=0)}（{current['us_level'] or '?'}），"
        f"EU AQI {num(current['eu_aqi'], digits=0)}（{current['eu_level'] or '?'}）",
        f"PM2.5 {num(current['pm2_5'])} µg/m³，PM10 {num(current['pm10'])}，O₃ {num(current['o3'])}，"
        f"NO₂ {num(current['no2'])}，SO₂ {num(current['so2'])}，CO {num(current['co'], digits=0)}，紫外线 {num(current['uv'])}",
    ]
    if days:
        lines += ["", "| 日期 | 当日最高 US AQI |", "|---|---|"]
        lines += [f"| {d['date']} | {d['us_aqi_max']}（{d['us_level']}）|" for d in days]
    return {"current": current, "daily": days}, lines, "空气质量是 CAMS 模型数据，不是站点实测"


def date_range(request):
    start, end = request["start_date"], request["end_date"] or request["start_date"]
    if not start:
        raise Failed(f"{request['query']} 要写 start_date（YYYY-MM-DD）")
    try:
        first = datetime.date.fromisoformat(start)
        last = datetime.date.fromisoformat(end)
    except ValueError:
        raise Failed("日期要写成 YYYY-MM-DD，例：2024-07-01")
    if last < first:
        raise Failed("end_date 早于 start_date")
    if (last - first).days >= MAX_RANGE_DAYS:
        raise Failed(f"一次至多查 {MAX_RANGE_DAYS} 天，分几次查")
    return start, end


def daily_rows(daily, fields):
    rows = []
    for i, date in enumerate(daily.get("time") or []):
        row = {"date": date}
        for key, name in fields.items():
            row[key] = column(daily, name, i)
        if "weather" in row:
            row["weather"] = weather_label(row["weather"])
        rows.append(row)
    return rows


def q_historical(place, request):
    start, end = date_range(request)
    if datetime.date.fromisoformat(end) > datetime.date.today() - datetime.timedelta(days=2):
        raise Failed("历史天气只到两天前；最近的用 forecast 查")
    data = cached_get(ARCHIVE_URL, {
        **coords(place), "start_date": start, "end_date": end,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
    }, ttl=30 * 24 * 3600)
    rows = daily_rows(data.get("daily") or {}, {
        "weather": "weather_code", "min_c": "temperature_2m_min", "max_c": "temperature_2m_max",
        "precip_mm": "precipitation_sum", "wind_max_kmh": "wind_speed_10m_max"})
    lines = ["| 日期 | 天气 | 气温 | 降水 | 最大风速 |", "|---|---|---|---|---|"]
    lines += [f"| {r['date']} | {r['weather']} | {num(r['min_c'])}~{num(r['max_c'], '°C')} | "
              f"{num(r['precip_mm'], ' mm')} | {num(r['wind_max_kmh'], ' km/h')} |" for r in rows]
    return {"daily": rows}, lines, "历史天气是 ERA5 再分析数据，不等于气象站实测"


def q_marine(place, request):
    data = cached_get(MARINE_URL, {
        **coords(place),
        "current": "wave_height,wave_direction,wave_period,sea_surface_temperature",
        "daily": "wave_height_max,wave_direction_dominant,wave_period_max,swell_wave_height_max",
        "forecast_days": min(request["days"], 7),
    })
    cur = data.get("current") or {}
    if cur.get("wave_height") is None:
        raise Failed(f"「{place['name']}」这个点没有海况数据（多半在内陆）；写一个海边的地名或港口")
    current = {"time": cur.get("time"), "wave_m": cur.get("wave_height"), "wave_dir": direction(cur.get("wave_direction")),
               "period_s": cur.get("wave_period"), "sea_temp_c": cur.get("sea_surface_temperature")}
    rows = daily_rows(data.get("daily") or {}, {
        "wave_max_m": "wave_height_max", "swell_max_m": "swell_wave_height_max", "period_max_s": "wave_period_max"})
    lines = [f"**现在**：浪高 {num(current['wave_m'], ' m')}，{current['wave_dir']}向浪，周期 {num(current['period_s'], ' s')}，"
             f"海表 {num(current['sea_temp_c'], '°C')}", "",
             "| 日期 | 最大浪高 | 最大涌浪 | 最长周期 |", "|---|---|---|---|"]
    lines += [f"| {r['date']} | {num(r['wave_max_m'], ' m')} | {num(r['swell_max_m'], ' m')} | {num(r['period_max_s'], ' s')} |"
              for r in rows]
    return {"current": current, "daily": rows}, lines, "海况是模型数据，不能代替官方海事预报"


def q_climate(place, request):
    start, end = date_range(request)
    data = cached_get(CLIMATE_URL, {
        **coords(place), "start_date": start, "end_date": end, "models": "EC_Earth3P_HR",
        "daily": "temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum",
    }, ttl=30 * 24 * 3600)
    rows = daily_rows(data.get("daily") or {}, {
        "mean_c": "temperature_2m_mean", "min_c": "temperature_2m_min", "max_c": "temperature_2m_max",
        "precip_mm": "precipitation_sum"})
    lines = ["| 日期 | 平均气温 | 气温 | 降水 |", "|---|---|---|---|"]
    lines += [f"| {r['date']} | {num(r['mean_c'], '°C')} | {num(r['min_c'])}~{num(r['max_c'], '°C')} | {num(r['precip_mm'], ' mm')} |"
              for r in rows]
    return {"model": "EC_Earth3P_HR", "daily": rows}, lines, \
        "气候数据是单个 CMIP6 模型（EC_Earth3P_HR）的模拟，只看趋势，不是某一天的天气预报"


def q_elevation(place, request):
    data = cached_get(ELEVATION_URL, {"latitude": place["latitude"], "longitude": place["longitude"]},
                      ttl=GEOCODING_TTL)
    values = data.get("elevation") or [place.get("elevation_m")]
    value = values[0] if values else None
    return {"elevation_m": value}, [f"海拔 **{num(value, ' 米', 0)}**"], "90 米分辨率数字高程模型"


HANDLERS = {"forecast": q_forecast, "air_quality": q_air_quality, "historical": q_historical,
            "marine": q_marine, "climate": q_climate, "elevation": q_elevation}
TITLES = {"forecast": "天气", "air_quality": "空气质量", "historical": "历史天气",
          "marine": "海况", "climate": "气候趋势", "elevation": "海拔"}


def wttr(location, reason):
    text = http_get(WTTR_URL + urllib.parse.quote(location) + "?format=%l:+%C+%t+%w+%h&lang=zh", as_json=False).strip()
    if not text or "Unknown location" in text:
        raise Failed(reason)
    return {"provider": "wttr.in", "fallback_reason": reason, "summary": text}, \
        [f"**现在**：{text}", f"（Open-Meteo 失败：{reason}；这是 wttr.in 的兜底结果，只有实时天气）"]


# ---------------------------------------------------------------- 入口

def parse(args):
    if not isinstance(args, dict):
        raise Failed("参数要是一个 JSON 对象")
    location = str(args.get("location") or "").strip()
    if not location:
        raise Failed("location 是空的：问用户要查哪个地方，再带上 location 重新调用")
    query = args.get("query") or "forecast"
    if query not in QUERIES:
        raise Failed(f"query 只能是 {' / '.join(QUERIES)}")
    days = args.get("days", 3)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 16:
        raise Failed("days 要是 1 到 16 的整数")
    country = str(args.get("country_code") or "").strip().upper() or None
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise Failed("country_code 要是两位字母，例：JP、US、CN")
    fmt = args.get("format") or "md"
    if fmt not in ("md", "json"):
        raise Failed("format 只能是 md 或 json")
    return {"location": location, "query": query, "days": days, "country_code": country, "format": fmt,
            "start_date": (args.get("start_date") or "").strip() or None,
            "end_date": (args.get("end_date") or "").strip() or None}


def run(args):
    request = parse(args)
    try:
        place, others, warning = resolve(request)
        data, lines, note = HANDLERS[request["query"]](place, request)
        provider = "Open-Meteo"
    except Failed as exc:
        if request["query"] != "forecast" or "找不到地点" in str(exc):
            raise
        place, others, warning, note, provider = None, [], None, None, "wttr.in"
        data, lines = wttr(request["location"], str(exc))
    if request["format"] == "json":
        return json.dumps({"query": request["query"], "location": place, "alternatives": others,
                           "warning": warning, "provider": provider, "note": note, **data}, ensure_ascii=False)
    head = f"## {place['name'] if place else request['location']} · {TITLES[request['query']]}"
    out = [head]
    if warning:
        out.append(f"⚠ {warning}")
    out += lines
    tail = []
    if others:
        tail.append("同名的还有：" + " / ".join(others) + "（不对就加 country_code 或写得更具体）")
    tail.append(f"数据：{provider}" + (f"；{note}" if note else ""))
    return "\n".join(out + [""] + tail)


def main():
    try:
        print(run(json.load(sys.stdin)))
    except json.JSONDecodeError as exc:
        print(f"错误：参数不是 JSON：{exc}")
        sys.exit(1)
    except Failed as exc:
        print(f"错误：{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
