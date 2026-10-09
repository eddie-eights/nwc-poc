"""app/dashboard/config.py が読む場所の段数（007 で web/ → app/dashboard/ に 1 段深くなった）の検査。
- .env はリポジトリの直下（HERE/../../.env）
- 相対の DATA_DIR はリポジトリの直下から（.env.example の DATA_DIR=app/agentcore/data）
- 手元ではエージェントのモジュールを app/agentcore/ から読む（sys.path に HERE/../agentcore）
config.py は import すると .env を読み、AWS_REGION が無ければ止まるので、import せずに文字列で照合し、
照合した式をこのファイルの場所から評価して、実在するパスになることを見る。
実行は python3 tests/test_dashboard_config.py（依存は無い）。"""
import os, re, sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HERE = os.path.join(ROOT, "app", "dashboard")
with open(os.path.join(HERE, "config.py"), encoding="utf-8") as f:
    src = f.read()

passed = 0
def check(name, cond):
    global passed
    if not cond:
        print(f"NG: {name}"); sys.exit(1)
    passed += 1; print(f"OK: {name}")

def join_args(pattern):  # config.py の os.path.join(HERE, …) の引数（文字列リテラル）を取り出す。見つからなければ None
    m = re.search(pattern, src, re.M)
    return re.findall(r'"([^"]*)"', m.group(1)) if m else None

env = join_args(r'^\s*path = os\.environ\.get\("ENV_FILE"\) or os\.path\.join\(HERE, ([^)]*)\)')
check(".env の既定はリポジトリの直下（HERE/../../.env）",
      env == ["..", "..", ".env"] and os.path.normpath(os.path.join(HERE, *env)) == os.path.join(ROOT, ".env"))

data = join_args(r'^\s*DATA_DIR = os\.path\.normpath\(os\.path\.join\(HERE, ([^)]*), DATA_DIR\)\)')
example = re.search(r"^DATA_DIR=(\S+)", open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read(), re.M)
check("相対の DATA_DIR はリポジトリの直下から（.env.example の DATA_DIR が実在するディレクトリになる）",
      data == ["..", ".."] and "if not os.path.isabs(DATA_DIR):" in src and example is not None
      and os.path.isdir(os.path.normpath(os.path.join(HERE, *data, example.group(1)))))

agent = join_args(r'^\s*sys\.path\.append\(os\.path\.join\(HERE, ([^)]*)\)\)')
check("手元ではエージェントのモジュールを app/agentcore/ から読む（sys.path に HERE/../agentcore）",
      agent == ["..", "agentcore"] and os.path.isfile(os.path.join(HERE, *agent, "topology.py"))
      and 'if not os.path.isfile(os.path.join(HERE, "topology.py")):' in src)

print(f"通過 {passed} / 失敗 0")
