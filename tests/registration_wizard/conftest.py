import sys
from pathlib import Path

# wizard.py uses bare `from config import ...` because it is designed to run
# from inside registration_wizard/.  Add that directory to sys.path so pytest
# resolves the import correctly when running from the gateway/ root.
_wizard_dir = str(Path(__file__).parents[2] / "registration_wizard")
if _wizard_dir not in sys.path:
    sys.path.insert(0, _wizard_dir)
