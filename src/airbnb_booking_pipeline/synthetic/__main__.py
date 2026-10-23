"""Regenerate the regression fixtures: python -m airbnb_booking_pipeline.synthetic [DIR]."""

import sys
from pathlib import Path

from .fixtures import write_fixtures

root = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures")
for path in write_fixtures(root):
    print(path)
