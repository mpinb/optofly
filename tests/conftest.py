import os

# Interactive test scripts that require a display — skip in headless/CI environments
collect_ignore = []

if not os.environ.get("DISPLAY"):
    collect_ignore.append("test_looming_edge_wrapping.py")
