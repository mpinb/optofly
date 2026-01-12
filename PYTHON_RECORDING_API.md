# Python API for Controlling Braid Recording

## Overview

The Braid web UI communicates with the backend via HTTP POST requests to the `/callback` endpoint. You can use Python scripts to programmatically control recording.

## API Endpoints

- **Braid URL (default):** `http://127.0.0.1:33333/`
- **Callback endpoint:** `http://127.0.0.1:33333/callback`

## Recording Controls

Send JSON payloads via POST to the callback endpoint:

### Start/Stop .braidz Recording (CSV tables)
```json
{"DoRecordCsvTables": true}   // Start recording
{"DoRecordCsvTables": false}  // Stop recording
```

### Start/Stop MP4 Video Recording (all cameras)
```json
{"DoRecordMp4Files": true}    // Start recording
{"DoRecordMp4Files": false}   // Stop recording
```

## Example Python Script

```python
#!/usr/bin/env python
import json
import time
import urllib.parse
import requests
import os

COOKIE_JAR_FNAME = "braid-cookies.json"

class BraidProxy:
    def __init__(self, braid_url):
        self.callback_url = urllib.parse.urljoin(braid_url, "callback")
        self.session = requests.session()

        # Load cookies if available
        if os.path.isfile(COOKIE_JAR_FNAME):
            with open(COOKIE_JAR_FNAME, 'r') as f:
                cookies = requests.utils.cookiejar_from_dict(json.load(f))
                self.session.cookies.update(cookies)

        r = self.session.get(braid_url)
        r.raise_for_status()

        # Store cookies
        with open(COOKIE_JAR_FNAME, 'w') as f:
            json.dump(requests.utils.dict_from_cookiejar(self.session.cookies), f)

    def send(self, cmd_dict):
        r = self.session.post(self.callback_url, json=cmd_dict)
        r.raise_for_status()

# Example usage
braid = BraidProxy(braid_url="http://127.0.0.1:33333/")

# Start MP4 recording
braid.send({"DoRecordMp4Files": True})

# Start .braidz recording
braid.send({"DoRecordCsvTables": True})

time.sleep(5.0)  # Record for 5 seconds

# Stop recordings
braid.send({"DoRecordMp4Files": False})
braid.send({"DoRecordCsvTables": False})
```

## Additional Examples

See the `strand-braid-user/scripts/` directory for more complete examples:
- `record-mp4-video-braid-all-cams.py` - Control MP4 recording for all cameras
- `record-mp4-video.py` - Control individual strand-cam recording

## Technical Details

- **Frontend code:** `braid/braid-run/braid_frontend/src/main.rs`
- **API types:** `braid/braid-types/src/lib.rs:990-1017` (enum `BraidHttpApiCallback`)
- **Backend handler:** `braid/braid-run/src/callback_handling.rs:87-114`
