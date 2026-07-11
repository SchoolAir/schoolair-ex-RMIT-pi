"""services/trigger.py

Previously sent SIGUSR1 to the sen6x C daemon to force an immediate JSON file
write before verification reads.  Retained for historical reference only.

The daemon has been replaced by sen6x_read, a one-shot subprocess invoked
directly by read_sensor().  Every read_sensor() call is already a fresh
hardware read, so there is nothing to trigger.
"""
