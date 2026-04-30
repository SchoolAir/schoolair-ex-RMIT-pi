# schoolair-pi
Raspberry Pi client for the SchoolAir platform.

## Development 

Install python virtual environment & install requirements?
```
python3 -m venv .venv
```

```bash
cp .env.example .env  # fill in AUTH_TOKEN and SERVER_URL after registering the device
```

Run in development:
NOTE: use mock to feed mock sensor data! Otherwise program will look for real sensor script.
```bash
npm run dev:mock
```
