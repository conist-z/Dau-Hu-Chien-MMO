"""One-off audit: compile all sources + import every package module."""
import compileall
import importlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ok = compileall.compile_dir(str(ROOT), quiet=1, force=True, rx=None)
print("COMPILE:", "OK" if ok else "FAILED")

sys.path.insert(0, str(ROOT))
SKIP = {"scripts", "tests", "infra", ".venv", "assets", "data", "docs", ".kilo"}
failed = []
for p in sorted(ROOT.rglob("*.py")):
    rel = p.relative_to(ROOT)
    if any(part in SKIP or part.startswith(".") for part in rel.parts):
        continue
    mod = ".".join(rel.with_suffix("").parts)
    if mod.endswith("__main__") or mod == "bot":
        continue
    try:
        importlib.import_module(mod)
    except Exception as e:  # noqa: BLE001
        failed.append(f"{mod}: {type(e).__name__}: {e}")
if failed:
    print("IMPORT FAILURES:")
    for f in failed:
        print("  -", f)
    sys.exit(1)
print("ALL_MODULE_IMPORTS_OK")
