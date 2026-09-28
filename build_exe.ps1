# Build a single-file exe. Run from the os-course-reader directory.
# Output: dist\ICS-Reader.exe
$ErrorActionPreference = "Stop"

pyinstaller --noconfirm --onefile --windowed --clean `
  --name "ICS-Reader" `
  --add-data "web;web" `
  --add-data "stubs;stubs" `
  --collect-all webview `
  --collect-all pythonnet `
  --collect-all clr_loader `
  --hidden-import clr_loader `
  app.py

Write-Host ""
Write-Host "Done: dist\ICS-Reader.exe"
Write-Host "On first run it reads/generates config.json next to the exe; lectures render into course\ next to the exe."
