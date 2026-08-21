"""trading-research decision memory substrate.

Records AI-generated trading analysis decisions, appends later validation events,
performs rolling review, and emits memory-derived feedback for the next report.
No broker access, no real trade execution, no external secrets.

This module is a thin re-export shim. The implementation was split (Task 6,
skill_optimization_plan_20260705.md) into ``memory_schema.py`` (data
structures/enums/validation), ``memory_store.py`` (connection/persistence
primitives), and ``memory_review.py`` (CLI commands/aggregation/statistics) to
keep every file under 800 lines. Existing importers of
``trading_memory_core`` keep working unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_schema import *  # noqa: F401,F403
from memory_store import *  # noqa: F401,F403
from memory_review import *  # noqa: F401,F403
