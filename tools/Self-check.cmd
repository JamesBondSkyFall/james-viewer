@echo off
rem Tries every part of James Viewer on this PC and saves a report on the Desktop.
start "" /wait "%~dp0James Viewer.exe" --self-check
