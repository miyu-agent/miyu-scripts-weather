# weather.py 的测试（审计 2026-11-18）。
#
# 跑法（在仓库根）：
#   python3 -I -B -m unittest discover -s .github/tests
# （不加 -t .：.github 不是合法的包名，加了 unittest 会报 Start directory is not importable。）
#
# 只用标准库；不联网：http_get 换成直接失败的桩，断网也能跑。HOME、XDG_*、MIYU_* 都指到临时目录。
# 放在 .github/tests/ 而不是 tests/：打包会跳过 .github，测试不会进包。

import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "weather.py"


def load():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("weather_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parameter_schema():
    """脚本头 `# Parameters:` 下面那段 JSON（核心照它给模型看参数说明）。"""
    lines, inside = [], False
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        if line.startswith("# Parameters:"):
            inside = True
            continue
        if inside:
            if not line.startswith("#"):
                break
            lines.append(line[1:])
    return json.loads("\n".join(lines))


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

    def call(self, args):
        """照 main() 跑一次：stdin 喂 JSON，返回 (退出码, 输出)。main() 只许以 SystemExit 结束。"""
        out = io.StringIO()
        code = 0
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(args))), redirect_stdout(out):
            try:
                self.mod.main()
            except SystemExit as exc:
                code = exc.code or 0
        return code, out.getvalue()


class IntegerDate(Sandbox):
    # 审计 #178 weather-a：parse 里 `(args.get("start_date") or "").strip()`，整数 20240701 抛
    # AttributeError: 'int' object has no attribute 'strip'；parse 在 run 的 try 外面、main 也不接，traceback 漏出来。
    def test_integer_start_date_gives_friendly_error(self):
        code, out = self.call({"location": "Tokyo", "query": "historical", "start_date": 20240701})
        self.assertEqual(code, 1, out)
        self.assertTrue(out.startswith("错误："), out)
        self.assertIn("start_date", out)
        self.assertEqual(self.calls, [], "参数不对，不该联网")


class RangeLimit(Sandbox):
    # 审计 #178 weather-b：参数说明写「At most 31 days after start_date」，代码 `(last - first).days >= 31` 就拒，
    # 07-01→08-01（正好晚 31 天）被拒，说明和代码差 1 天。下面断言二者一致：
    # 说明还写 after start_date 时，08-01 必须被接受；说明改成含首尾的说法时，31 天（含首尾）是上限。
    def test_description_matches_code(self):
        desc = parameter_schema()["properties"]["end_date"]["description"]
        request = {"query": "historical", "start_date": "2024-07-01"}
        if "after start_date" in desc:
            try:
                got = self.mod.date_range(dict(request, end_date="2024-08-01"))
            except self.mod.Failed as exc:
                self.fail(f"说明是「{desc}」，晚 31 天的 end_date 应被接受，却报：{exc}")
            self.assertEqual(got, ("2024-07-01", "2024-08-01"))
        else:
            self.assertIn("31", desc)
            self.assertEqual(self.mod.date_range(dict(request, end_date="2024-07-31")), ("2024-07-01", "2024-07-31"))
            with self.assertRaises(self.mod.Failed):
                self.mod.date_range(dict(request, end_date="2024-08-01"))


if __name__ == "__main__":
    unittest.main()
