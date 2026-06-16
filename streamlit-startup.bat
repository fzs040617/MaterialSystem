@echo off
setlocal

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo Starting MaterialSystem...
echo Project directory: %CD%

if not exist ".\venv\Scripts\python.exe" (
    echo.
    echo ERROR: Python virtual environment was not found.
    echo Expected: .\venv\Scripts\python.exe
    echo Please create or restore the venv, then run this script again.
    echo.
    pause
    exit /b 1
)

if exist ".\venv\Scripts\streamlit.exe" (
    echo Using .\venv\Scripts\streamlit.exe
    ".\venv\Scripts\streamlit.exe" run ".\app.py" --server.port 8501
) else (
    echo streamlit.exe was not found. Trying python -m streamlit...
    ".\venv\Scripts\python.exe" -m streamlit run ".\app.py" --server.port 8501
)

set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo ERROR: Streamlit exited with code %EXIT_CODE%.
    echo Please review the error message above.
    echo.
    pause
    exit /b %EXIT_CODE%
)

endlocal
