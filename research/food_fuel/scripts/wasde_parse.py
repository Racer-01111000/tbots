"""Compatibility shim: the parsers live in ffmod/wasde.py (importable by the brief generator and tests)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ffmod.wasde import *  # noqa: F401,F403
from ffmod.wasde import parse_txt, parse_xml, flatten_xml  # noqa: F401
