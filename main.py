#!/usr/bin/env python3
"""
main.py — AudioScribe entry point.
Starts the FastAPI web server (which also manages the Telegram bot).
"""
import logging
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web.app import run

if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )
    run()
