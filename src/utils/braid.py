import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests


COOKIE_JAR_FNAME = "braid-cookies.json"


class BraidProxy:
    """Proxy for controlling Braid recording via HTTP API.

    Braid exposes two HTTP servers:
    - Events/SSE endpoint (e.g., port 8397) for streaming tracking data
    - UI endpoint (e.g., port 12345) with the /callback API for control

    The callback_url should point to the UI endpoint.
    """

    def __init__(self, callback_url: str):
        """Initialize Braid proxy.

        Args:
            callback_url: Base URL of Braid UI server (e.g., http://127.0.0.1:12345/)
        """
        self.base_url = callback_url
        self.callback_url = urllib.parse.urljoin(callback_url, "callback")
        self.session = requests.session()

        # Load cookies if available
        if os.path.isfile(COOKIE_JAR_FNAME):
            with open(COOKIE_JAR_FNAME, "r") as f:
                cookies = requests.utils.cookiejar_from_dict(json.load(f))
                self.session.cookies.update(cookies)

        # Connect to Braid (raises exception if not running)
        try:
            r = self.session.get(callback_url, timeout=5)
            r.raise_for_status()
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Could not connect to Braid at {callback_url}. Is braid-run running?"
            )
        except requests.exceptions.Timeout:
            raise TimeoutError(f"Connection to Braid at {callback_url} timed out")

        # Store cookies
        with open(COOKIE_JAR_FNAME, "w") as f:
            json.dump(requests.utils.dict_from_cookiejar(self.session.cookies), f)

    def send(self, cmd_dict: dict) -> None:
        """Send command to Braid callback endpoint.

        Args:
            cmd_dict: Command dictionary (e.g., {"DoRecordCsvTables": True})

        Raises:
            requests.HTTPError: If command fails
        """
        r = self.session.post(self.callback_url, json=cmd_dict, timeout=10)
        r.raise_for_status()

    def start_csv_recording(self) -> None:
        """Start CSV table recording (.braidz format)."""
        self.send({"DoRecordCsvTables": True})

    def stop_csv_recording(self) -> None:
        """Stop CSV table recording."""
        self.send({"DoRecordCsvTables": False})


def check_braid_folder_exists(
    root_path: str = "/mnt/data/experiments/",
    callback_url: Optional[str] = None,
    auto_start_recording: bool = True,
) -> tuple[str, Optional[BraidProxy]]:
    """
    Start a fresh Braid CSV recording and return its folder.

    When auto_start_recording is True (the only mode main.py uses), this
    always starts a new recording via the Braid callback API and waits for
    a genuinely new folder to appear -- it does not reuse any existing
    same-day folder. A folder existing does not mean Braid is currently
    recording into it: the process that created it may have already
    stopped recording on exit, so trusting folder presence alone silently
    dropped all Braid tracking data on a second run of the same day.

    When auto_start_recording is False, this only checks for an existing
    same-day folder and returns it with no proxy -- unchanged from before.

    Parameters:
        root_path: Root path to check for braid folders
        callback_url: URL of Braid UI server (e.g., http://127.0.0.1:12345/)
        auto_start_recording: If True, always start a fresh recording. If
            False, only look for an existing same-day folder.

    Returns:
        Tuple of (braid_folder_path, braid_proxy_instance).
        braid_proxy is None only when auto_start_recording is False.

    Raises:
        SystemExit: If root_path doesn't exist, connection fails, or
            recording start fails.
    """
    if not os.path.exists(root_path):
        print(f"\n{'=' * 70}")
        print("ERROR: Braid experiments root path does not exist")
        print(f"{'=' * 70}")
        print(f"Path: {root_path}")
        print("\nPlease verify the experiments path is mounted and accessible.")
        print(f"{'=' * 70}\n")
        sys.exit(1)

    today = datetime.now().strftime("%Y%m%d")
    pattern = re.compile(f"^{today}_\\d{{6}}\\.braid$")

    def find_matching_folders():
        """Helper to find matching .braid folders."""
        matching = []
        try:
            for item in os.listdir(root_path):
                full_path = os.path.join(root_path, item)
                if pattern.match(item) and os.path.isdir(full_path):
                    matching.append((item, full_path))
        except PermissionError:
            print(f"\n{'=' * 70}")
            print("ERROR: Permission denied accessing experiments folder")
            print(f"{'=' * 70}")
            print(f"Path: {root_path}")
            print("\nPlease check folder permissions.")
            print(f"{'=' * 70}\n")
            sys.exit(1)
        return matching

    if not auto_start_recording:
        print(f"Checking for braid folder with date {today} in {root_path}...")
        matching_folders = find_matching_folders()
        if matching_folders:
            matching_folders.sort(reverse=True)
            if len(matching_folders) > 1:
                print(
                    f"Multiple braid folders found. Using most recent: {matching_folders[0][0]}"
                )
            braid_folder = matching_folders[0][1]
            print(f"✓ Found existing braid folder: {braid_folder}")
            return braid_folder, None

        print(f"\n{'=' * 70}")
        print("ERROR: No Braid recording folder found for today")
        print(f"{'=' * 70}")
        print(f"Expected folder pattern: {today}_HHMMSS.braid")
        print(f"Searched in: {root_path}")
        print("\nPlease start Braid recording BEFORE running this script.")
        print(f"{'=' * 70}\n")
        sys.exit(1)

    if not callback_url:
        print(f"\n{'=' * 70}")
        print("ERROR: No Braid callback URL provided")
        print(f"{'=' * 70}")
        print("Cannot start recording without Braid callback URL.")
        print(f"{'=' * 70}\n")
        sys.exit(1)

    print("\nStarting Braid recording...")
    print(f"Connecting to Braid at {callback_url}...")

    existing_folders = {full_path for _, full_path in find_matching_folders()}

    try:
        braid = BraidProxy(callback_url)
        print("✓ Connected to Braid")

        print("Starting CSV recording...")
        braid.start_csv_recording()
        print("✓ Recording started")

        print(f"Waiting for a new .braid folder to appear in {root_path}...")
        max_wait = 10
        start_time = time.time()

        while time.time() - start_time < max_wait:
            current_folders = find_matching_folders()
            new_folders = [
                (name, path)
                for name, path in current_folders
                if path not in existing_folders
            ]
            if new_folders:
                new_folders.sort(reverse=True)
                braid_folder = new_folders[0][1]
                print(f"✓ Recording folder created: {braid_folder}")
                return braid_folder, braid

            time.sleep(0.5)

        print(f"\n{'=' * 70}")
        print("ERROR: Recording started but no new .braid folder was created")
        print(f"{'=' * 70}")
        print(f"Waited {max_wait} seconds for a new folder to appear in {root_path}")
        print("The recording may not be working correctly.")
        print(f"{'=' * 70}\n")
        sys.exit(1)

    except (ConnectionError, TimeoutError) as e:
        print(f"\n{'=' * 70}")
        print("ERROR: Could not connect to Braid")
        print(f"{'=' * 70}")
        print(f"{e}")
        print("\nPlease ensure:")
        print("  1. Braid is running (braid-run ...)")
        print(f"  2. Braid is accessible at {callback_url}")
        print(f"{'=' * 70}\n")
        sys.exit(1)

    except Exception as e:
        print(f"\n{'=' * 70}")
        print("ERROR: Failed to start Braid recording")
        print(f"{'=' * 70}")
        print(f"{e}")
        print(f"{'=' * 70}\n")
        sys.exit(1)


def verify_csv_files_in_braid(braid_folder: str) -> None:
    """Verify that CSV files generated by OptoFly are present in the .braid folder.

    Lists any CSV files found (e.g. opto.csv written by OptoTriggerWorker) so
    the operator can confirm data was recorded. Does not copy or move anything.

    Args:
        braid_folder: Path to the .braid recording folder
    """
    try:
        braid_path = Path(braid_folder)
        if not braid_path.exists():
            print(f"WARNING: Braid folder does not exist: {braid_folder}")
            return

        # Find all CSV files in braid folder (opto.csv, etc.)
        csv_files = list(braid_path.glob("*.csv"))

        if not csv_files:
            print("No CSV files found to copy")
            return

        print(f"Found {len(csv_files)} CSV file(s) in {braid_folder}")
        for csv_file in csv_files:
            print(f"  ✓ {csv_file.name}")

    except Exception as e:
        print(f"WARNING: Error checking CSV files: {e}")


if __name__ == "__main__":
    # Example usage
    braid_folder = check_braid_folder_exists("/home/buchsbaum/")
    print(f"Braid folder found: {braid_folder}")
