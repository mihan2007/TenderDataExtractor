import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "examples"))
# Tests must not depend on a developer's local config.json.
os.environ["TDE_CONFIG"] = os.path.join(ROOT, "config.example.json")
