@echo off
title Nyra - Cloudflare Tunnel
color 0B

echo.
echo  =====================================================
echo   Nyra Remote -- Cloudflare Tunnel Setup
echo  =====================================================
echo.

:: Check if cloudflared is installed
where cloudflared >nul 2>nul
if %errorlevel% neq 0 (
    echo  [!] cloudflared not found. Downloading...
    echo.
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe'"
    if not exist cloudflared.exe (
        echo  [ERROR] Download failed. Please download manually from:
        echo  https://github.com/cloudflare/cloudflared/releases/latest
        pause
        exit /b 1
    )
    echo  [OK] cloudflared downloaded.
    echo.
    set CF=cloudflared.exe
) else (
    set CF=cloudflared
)

echo  [*] Starting tunnel on port 7437...
echo  [*] Your public URL will appear below.
echo  [*] Share the URL + your token with your phone.
echo  [*] Press Ctrl+C to stop the tunnel.
echo.
echo  =====================================================
echo.

%CF% tunnel --url http://localhost:7437

echo.
echo  Tunnel closed.
pause
