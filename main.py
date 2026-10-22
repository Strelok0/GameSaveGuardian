"""
Main entry point for Game Save Guardian Windows desktop application.
"""

import argparse
import sys
from pathlib import Path

# Ensure 'src' is importable when running from root or PyInstaller frozen bundle
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from src.app import run_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Game Save Guardian")
    parser.add_argument("--minimized", action="store_true", help="Launch minimized to the Windows system tray")
    parser.add_argument("--startup", action="store_true", help="Indicates launch from Windows startup")
    args, _ = parser.parse_known_args()

    sys.exit(run_app(minimized=(args.minimized or args.startup)))
