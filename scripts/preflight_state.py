from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from admission_radar.config import load_config
from admission_radar.state import preflight
import argparse

p = argparse.ArgumentParser()
p.add_argument("--config", required=True)
args = p.parse_args()
try:
    config = load_config(args.config, network_only=True)
    preflight(config.database_path, tuple(w.id for w in config.websites))
except Exception as exc:
    print("历史数据库前置检查失败：" + type(exc).__name__, file=sys.stderr)
    raise SystemExit(1)
print("历史数据库前置检查通过。")
