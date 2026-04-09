import zmq
import json
from dataclasses import dataclass
import random
import time
import argparse

# ZONE_ENTER message format:
# {"obj_id": 1, "frame": 12345, "timestamp": 1234.56, "x": 0.01, "y": -0.02, "z": 0.18, "mean_heading": 0.52}


@dataclass
class ZoneEnterEvent:
    obj_id: int
    frame: int
    timestamp: float
    x: float
    y: float
    z: float
    mean_heading: float


def generate_zone_events(n=1000, seed=42):
    random.seed(seed)

    events = []
    for i in range(n):
        event = ZoneEnterEvent(
            obj_id=random.randint(1, 10),
            frame=random.randint(0, 10000),
            timestamp=time.time() + i * 10,
            x=random.uniform(-0.07, 0.07),
            y=random.uniform(-0.045, 0.06),
            z=random.uniform(0.15, 0.25),
            mean_heading=random.uniform(-3.14, 3.14),
        )
        events.append(event)
    return events


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send test ZONE_ENTER events via ZMQ.")
    parser.add_argument("--address", type=str, default="tcp://localhost:23456")
    args = parser.parse_args()

    context = zmq.Context()
    publisher = context.socket(zmq.PUB)
    publisher.bind(args.address)

    events = generate_zone_events(100)

    print("Starting to send ZONE_ENTER events...")
    for event in events:
        message_data = {
            "obj_id": event.obj_id,
            "frame": event.frame,
            "timestamp": event.timestamp,
            "x": event.x,
            "y": event.y,
            "z": event.z,
            "mean_heading": event.mean_heading,
        }
        message = json.dumps(message_data)
        print(f"Sending ZONE_ENTER for obj_id {event.obj_id} at frame {event.frame}")
        publisher.send_multipart([b"ZONE_ENTER", message.encode("utf-8")])
        time.sleep(10)
    print("Finished sending events.")
    publisher.close()
    context.term()
