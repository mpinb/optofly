import os
import re
import time
from datetime import datetime


def check_braid_folder_exists(
    root_path: str = "/home/buchsbaum/mnt/DATA/Experiments/",
) -> str:
    """
    A function that blocks the execution if a braid folder with the following structure is not found:
    `YYYMMDD_HHMMSS.braid`
    Where we don't care about the time, just the date.

    Parameters:
    root_path (str): The root path to check for the braid folder.

    Returns:
    braid_folder (str): The path to the braid folder once it exists.
    """
    # Get current date in YYYYMMDD format
    today = datetime.now().strftime("%Y%m%d")

    # Create regex pattern to match braid folders with today's date
    pattern = re.compile(f"^{today}_\\d{{6}}\\.braid$")

    # Infinite loop to wait for folder creation
    print(f"Waiting for braid folder with date {today}...")
    while True:
        # Check if any folder in root_path matches the pattern
        for item in os.listdir(root_path):
            if pattern.match(item) and os.path.isdir(os.path.join(root_path, item)):
                return os.path.join(root_path, item)

        # No matching folder found, wait before checking again
        time.sleep(5)  # Wait for 5 seconds before checking again


if __name__ == "__main__":
    # Example usage
    braid_folder = check_braid_folder_exists("/home/buchsbaum/")
    print(f"Braid folder found: {braid_folder}")
