"""Compatibility import path for the de-lined clean sheet operation."""

from .cleaning import (  # noqa: F401
    clean,
    clean_copy,
    clean_image,
    clean_png,
    clean_sheet,
    deline,
    line_mask,
    minimum_run,
    remove_lines,
    remove_staff_lines,
    strip_staff_lines,
)

remove_staff_lines = clean_sheet
