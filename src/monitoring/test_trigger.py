import zmq
import json
from dataclasses import dataclass
import random
import time
import argparse
# Trigger format
"""
message_data = {
    "obj_id": tracked_obj.obj_id,
    "frame": tracked_obj.current_frame,
    "braid_timestamp": tracked_obj.current_timestamp,
    "trigger_timestamp": time.time(),
    "mean_heading": mean_heading,
    # Keep old 'timestamp' field for backward compatibility
    "timestamp": tracked_obj.current_timestamp,
}

message = json.dumps(message_data)
self.publisher.send_string(f"{self.config.zmq.trigger_topic} {message}")
"""
@dataclass
class Trigger:
    obj_id: int
    frame: int
    braid_timestamp: float
    trigger_timestamp: float
    mean_heading: float
    timestamp: float
    
def generate_triggers(n=1000, seed=42):
    random.seed(seed)

    triggers = []
    for i in range(n):
        obj_id = random.randint(1, 10)
        frame = random.randint(0, 10000)
        braid_timestamp = random.uniform(1_600_000_000, 1_700_000_000)
        trigger_timestamp = braid_timestamp + random.uniform(0, 1)
        mean_heading = random.uniform(0, 360)
        timestamp = braid_timestamp  # For backward compatibility

        trigger = Trigger(
            obj_id=obj_id,
            frame=frame,
            braid_timestamp=braid_timestamp,
            trigger_timestamp=trigger_timestamp,
            mean_heading=mean_heading,
            timestamp=timestamp
        )
        triggers.append(trigger)
    return triggers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send test triggers via ZMQ.")
    parser.add_argument('--address', type=str, default="tcp://localhost:23456")
    args = parser.parse_args()

    context = zmq.Context()
    publisher = context.socket(zmq.PUB)
    publisher.bind(args.address)
    
    triggers = generate_triggers(100)

    print("Starting to send triggers...")
    for trigger in triggers:
        message_data = {
            "obj_id": trigger.obj_id,
            "frame": trigger.frame,
            "braid_timestamp": trigger.braid_timestamp,
            "trigger_timestamp": trigger.trigger_timestamp,
            "mean_heading": trigger.mean_heading,
            "timestamp": trigger.timestamp,
        }
        message = json.dumps(message_data)
        print(f"Sending trigger for obj_id {trigger.obj_id} at frame {trigger.frame}")
        publisher.send_string(f"TRIGGER {message}")
        time.sleep(10)  # Sleep to simulate time between triggers
    print("Finished sending triggers.")
    publisher.close()
    context.term()

