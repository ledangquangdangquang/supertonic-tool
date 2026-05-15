@echo off
title Supertonic - Sync from upstream
echo ============================================
echo   Sync from upstream (supertone-inc/supertonic)
echo   then push to origin (thanhng8/supertonic-tool)
echo ============================================
echo.

cd /d "%~dp0.."

echo [1/3] Fetching upstream...
git fetch upstream
if errorlevel 1 goto :error

echo.
echo [2/3] Merging upstream/main into local main...
git merge upstream/main --no-edit
if errorlevel 1 (
    echo.
    echo *** MERGE CONFLICT detected ***
    echo Fix conflicts manually, then run:
    echo   git add .
    echo   git commit
    echo   git push origin main
    goto :end
)

echo.
echo [3/3] Pushing to origin...
git push origin main
if errorlevel 1 goto :error

echo.
echo ============================================
echo   Done! Repo synced successfully.
echo ============================================
goto :end

:error
echo.
echo *** ERROR: Command failed. Check output above. ***

:end
echo.
pause
