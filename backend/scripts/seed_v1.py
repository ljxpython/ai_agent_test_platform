#!/usr/bin/env python3

"""Seed v1 defaults into Postgres.

Requires DATABASE_URL.
"""

from __future__ import annotations

from omo_platform.db.seed_v1 import seed_v1_defaults


if __name__ == "__main__":
    seed_v1_defaults()
