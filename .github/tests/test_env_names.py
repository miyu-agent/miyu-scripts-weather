# weather.py 读新环境变量名的测试（审计 2026-11-18 #168）。
#
# 跑法（在仓库根）：
#   python3 -I -B -m unittest discover -s .github/tests
# （不加 -t .：.github 不是合法的包名，加了 unittest 会报 Start directory is not importable。）
#
# 只用标准库；不联网：http_get 换成桩，断网也能跑。HOME、XDG_*、MIYU_* 都指到临时目录。
# 放在 .github/tests/ 而不是 tests/：打包会跳过 .github，测试不会进包。

import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "weather.py"

TOKYO = [{"name": "東京", "latitude": 35.6895, "longitude": 139.69171, "country_code": "JP",
          "population": 8336599, "feature_code": "PPLC", "timezone": "Asia/Tokyo"}]


def load():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("weather_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Sandbox(unittest.TestCase):
    """每条测试一个临时目录，环境里去掉所有 MIYU_*，网络函数先换成直接失败的桩。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MIYU_", "XDG_"))}
        env.update(HOME=str(root / "home"), XDG_CONFIG_HOME=str(root / "cfg"),
                   XDG_CACHE_HOME=str(root / "xdg-cache"), XDG_STATE_HOME=str(root / "xdg-state"),
                   XDG_DATA_HOME=str(root / "xdg-data"))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = root
        self.mod = load()
        self.calls = []

        def no_network(url, params=None, as_json=True):
            self.calls.append(url)
            raise AssertionError(f"测试里不该联网：{url}")

        self.mod.http_get = no_network


class EnvNames(Sandbox):
    # 审计 #168：核心 FH-6 以后只给新名字 MIYU_CACHE_DIR，不再给 MIYU_SCRIPT_CACHE_DIR。
    # 脚本两处只读旧名：cache_put（写缓存）和 cache_get（读缓存）。

    def setUp(self):
        super().setUp()
        self.cache = self.root / "cache"
        self.cache.mkdir()
        os.environ["MIYU_CACHE_DIR"] = str(self.cache)
        self.assertNotIn("MIYU_SCRIPT_CACHE_DIR", os.environ)
        self.key = "tokyo|JP"

    def test_cache_put_new_name(self):
        # 查过的地名应写进 MIYU_CACHE_DIR/geocoding。现在 cache_put 读不到目录，什么都不写。
        def fake_http_get(url, params=None, as_json=True):
            self.calls.append(url)
            return {"results": TOKYO}

        self.mod.http_get = fake_http_get
        self.assertEqual(self.mod.geocode("Tokyo", "JP")[0]["name"], "東京")
        name = hashlib.sha1(self.key.encode()).hexdigest() + ".json"
        self.assertTrue((self.cache / "geocoding" / name).is_file(),
                        f"MIYU_CACHE_DIR 里没有地名缓存：{sorted(os.listdir(self.cache))}")

    def test_cache_get_new_name(self):
        # MIYU_CACHE_DIR 里已有这个地名的缓存，geocode 应直接用、不联网。现在 cache_get 读不到目录，去联网，撞上桩。
        folder = self.cache / "geocoding"
        folder.mkdir()
        (folder / (hashlib.sha1(self.key.encode()).hexdigest() + ".json")).write_text(
            json.dumps(TOKYO, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.mod.geocode("Tokyo", "JP")[0]["name"], "東京")
        self.assertEqual(self.calls, [], "缓存在 MIYU_CACHE_DIR 里，不该联网")


if __name__ == "__main__":
    unittest.main()
