@echo off
rem Start GymCut reachable over the internet (Cloudflare quick tunnel) - e.g. to stream Live from the gym.
rem The internet address + QR code appear in the app under "Connect phone". Keep this PC on.
call "%~dp0run.bat" --internet %*
