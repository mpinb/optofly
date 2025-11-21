import os
import re
import sys
from datetime import datetime


def check_braid_folder_exists(
    root_path: str = "/mnt/data/experiments/",
) -> str:
    """
    Check if a braid folder with today's date exists.

    The function looks for a folder with the structure: YYYYMMDD_HHMMSS.braid
    where YYYYMMDD matches today's date.

    If no matching folder is found, the script exits immediately with an error message
    instructing the user to start Braid recording before running the experiment.

    Parameters:
        root_path (str): The root path to check for the braid folder.

    Returns:
        str: The full path to the braid folder if it exists.

    Raises:
        SystemExit: If no matching braid folder is found or root_path doesn't exist.
    """
    # Check if root path exists
    if not os.path.exists(root_path):
        print(f"\n{'='*70}")
        print("ERROR: Braid experiments root path does not exist")
        print(f"{'='*70}")
        print(f"Path: {root_path}")
        print("\nPlease verify the experiments path is mounted and accessible.")
        print(f"{'='*70}\n")
        sys.exit(1)

    # Get current date in YYYYMMDD format
    today = datetime.now().strftime("%Y%m%d")

    # Create regex pattern to match braid folders with today's date
    pattern = re.compile(f"^{today}_\\d{{6}}\\.braid$")

    # Check if any folder in root_path matches the pattern
    print(f"Checking for braid folder with date {today} in {root_path}...")
    matching_folders = []

    try:
        for item in os.listdir(root_path):
            full_path = os.path.join(root_path, item)
            if pattern.match(item) and os.path.isdir(full_path):
                matching_folders.append((item, full_path))
    except PermissionError:
        print(f"\n{'='*70}")
        print("ERROR: Permission denied accessing experiments folder")
        print(f"{'='*70}")
        print(f"Path: {root_path}")
        print("\nPlease check folder permissions.")
        print(f"{'='*70}\n")
        sys.exit(1)

    if not matching_folders:
        # No matching folder found - exit immediately
        print(f"\n{'='*70}")
        print("ERROR: No Braid recording folder found for today")
        print(f"{'='*70}")
        print(f"Expected folder pattern: {today}_HHMMSS.braid")
        print(f"Searched in: {root_path}")
        print("\nPlease start Braid recording BEFORE running this script.")
        print("Steps:")
        print("  1. Start Braid tracking system")
        print("  2. Begin recording (this creates the .braid folder)")
        print("  3. Run this script")
        print(f"{'='*70}\n")
        sys.exit(1)

    # If multiple folders exist, use the most recent one
    if len(matching_folders) > 1:
        matching_folders.sort(reverse=True)  # Sort by folder name (timestamp)
        print(f"Multiple braid folders found. Using most recent: {matching_folders[0][0]}")

    braid_folder = matching_folders[0][1]
    print(f"✓ Found braid folder: {braid_folder}")
    return braid_folder


if __name__ == "__main__":
    # Example usage
    braid_folder = check_braid_folder_exists("/home/buchsbaum/")
    print(f"Braid folder found: {braid_folder}")
