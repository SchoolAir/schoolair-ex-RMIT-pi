#!/usr/bin/env python3
import json, subprocess, time, sys
import matplotlib.pyplot as plt
from collections import deque
from pathlib import Path

MOCK_SENSOR = Path(__file__).parent / "mock-sensor.sh"
STATE_FILE  = Path(__file__).parent / "spike_state.json"
METRICS = ["co2", "pm25", "temp", "humidity"] # for sen6x only, for simplicity
HISTORY = 50
history = {m: deque([0] * HISTORY, maxlen=HISTORY) for m in METRICS}

def read_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except:
        return {"spiking": False}

plt.ion()
fig, axes = plt.subplots(2, 2, figsize=(10, 6))
axes = axes.flatten()

while True:
    result = subprocess.run([str(MOCK_SENSOR)], capture_output=True, text=True)
    reading = json.loads(result.stdout)["sen6x"]
    state = read_state()

    for i, metric in enumerate(METRICS):
        history[metric].append(reading[metric])
        axes[i].cla()
        axes[i].plot(history[metric])
        axes[i].set_title(f"{metric} {'spike!' if state['spiking'] else 'normal'}")
        axes[i].set_ylim(bottom=0)

    fig.suptitle("Sensor Preview")
    plt.tight_layout()
    plt.pause(1)