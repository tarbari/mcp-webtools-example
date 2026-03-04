"""Auto-discover and import all tool modules to trigger @mcp.tool() registration."""
import importlib
import pkgutil
from pathlib import Path

for _info in pkgutil.iter_modules([str(Path(__file__).parent)]):
    importlib.import_module(f"{__name__}.{_info.name}")
