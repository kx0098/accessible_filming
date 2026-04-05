# accessible_filming

## Live Stream + Record Button (Unified)

Run the merged app from the project root:

```bash
python3 mjpeg_server.py
```

This single program:
- Opens the camera once
- Serves a live MJPEG stream on your iPad via a local URL (printed at startup)
- Allows GPIO17 record button to start/stop video recording while streaming
- Saves recordings to the `recordings/` folder with timestamped filenames

Keep the browser page open on your iPad to see the live feed. Press the physical record button to toggle recording on/off while the stream stays active.
