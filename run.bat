@echo off
REM =========================================================================
REM run.bat - Lanzador del sistema de deteccion de anomalias Moodle (Windows)
REM =========================================================================
REM Uso:
REM   run.bat                     -> sistema completo
REM   run.bat check               -> verificar conexiones
REM   run.bat capture             -> solo captura de logs
REM   run.bat preprocess          -> solo preprocesador
REM   run.bat engine              -> solo motor analitico
REM   run.bat infra-up            -> levantar Redis + InfluxDB + Grafana
REM   run.bat infra-down          -> detener la infraestructura
REM   run.bat setup-influx        -> configurar InfluxDB por primera vez
REM =========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

set PYTHON=python
set SCRIPTS=scripts

if "%1"=="check" (
    echo [Verificando conexiones...]
    %PYTHON% %SCRIPTS%\check_connections.py
    goto :eof
)

if "%1"=="capture" (
    echo [Iniciando captura de logs...]
    %PYTHON% %SCRIPTS%\run_system.py --capture
    goto :eof
)

if "%1"=="preprocess" (
    echo [Iniciando preprocesador...]
    %PYTHON% %SCRIPTS%\run_system.py --preprocess
    goto :eof
)

if "%1"=="engine" (
    echo [Iniciando motor analitico...]
    %PYTHON% %SCRIPTS%\run_system.py --engine
    goto :eof
)

if "%1"=="infra-up" (
    echo [Levantando Redis + InfluxDB + Grafana con Docker Compose...]
    docker compose up -d
    echo.
    echo Servicios disponibles:
    echo   Redis     -> localhost:6379
    echo   InfluxDB  -> http://localhost:8086
    echo   Grafana   -> http://localhost:3000  (admin / admin)
    goto :eof
)

if "%1"=="infra-down" (
    echo [Deteniendo infraestructura Docker...]
    docker compose down
    goto :eof
)

if "%1"=="setup-influx" (
    echo [Configurando InfluxDB...]
    %PYTHON% %SCRIPTS%\setup_influxdb.py --admin-token moodle-influx-token-2024
    goto :eof
)

REM Sistema completo (default)
echo [Iniciando sistema completo de deteccion de anomalias...]
%PYTHON% main.py
