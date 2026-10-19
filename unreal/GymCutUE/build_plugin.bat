@echo off
rem Rebuild the GymCut Live Link plugin (needs VS 2022 17.14 / VS 2026 C++ tools). Close the editor first.
set UE=F:\Gamez\Epic\UE_5.8
call "%UE%\Engine\Build\BatchFiles\RunUAT.bat" BuildPlugin -Plugin="%~dp0Plugins\GymCutLiveLink\GymCutLiveLink.uplugin" -Package="%~dp0Saved\PluginBuild\GymCutLiveLink" -TargetPlatforms=Win64 -HostPlatforms=Win64 || exit /b 1
xcopy /y /e /i "%~dp0Saved\PluginBuild\GymCutLiveLink\Binaries" "%~dp0Plugins\GymCutLiveLink\Binaries"
echo GymCut Live Link plugin rebuilt.
