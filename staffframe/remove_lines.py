"""Compatibility import path for staff-line removal."""

from .cleaning import (  # noqa: F401
    clean,
    clean_png,
    clean_sheet,
    line_mask,
    remove_lines,
    remove_staff_lines,
)

remove_staff_lines = clean_sheet
remove_lines = clean_sheet
