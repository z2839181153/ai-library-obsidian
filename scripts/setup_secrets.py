"""把 QwenPaw 已配置的 ModelScope key 写入项目 data/secrets.json（不入库、不打印 key）。"""
import json
import os
import pathlib

src = os.path.expanduser(r"~\.qwenpaw.secret\providers\builtin\modelscope.json")
d = json.load(open(src, encoding="utf-8"))
key = d.get("api_key") or d.get("key") or ""
print("key_len:", len(key), "prefix:", key[:6] if key else "NONE")

cfg = json.load(open("config/settings.json", encoding="utf-8"))
data_dir = cfg.get("paths", {}).get("data_dir", "data")
p = pathlib.Path(data_dir) / "secrets.json"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({"modelscope_api_key": key}, ensure_ascii=False, indent=2), encoding="utf-8")
print("secrets written to", p)
