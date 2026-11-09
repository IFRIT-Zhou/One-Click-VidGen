"""Bounded capacity shared by layered inventory and poster assembly.

The renderer must accommodate every allowed label with its own plate:
1 background + 8 subjects + 16 decorations + 2 * 48 labels = 121 layers.
This does not change model-call budgets, image budgets, or code sandbox limits.
"""

MAX_INVENTORY_TEXTS = 32
MAX_LABELS = 48
MAX_SUBJECTS = 8
MAX_DECORATIONS = 16
MAX_LAYERS = 128  # Kept in sync with lib/layered-contract.mjs by contract tests.
MAX_MOTION_GROUPS = MAX_SUBJECTS + MAX_LABELS + MAX_DECORATIONS
