"""
scripts/run_system.py
=====================
Punto de entrada alternativo con menú de arranque interactivo.
Permite iniciar el sistema completo o sus subsistemas de forma independiente.

Uso:
    python scripts/run_system.py              # sistema completo
    python scripts/run_system.py --capture    # solo captura de logs
    python scripts/run_system.py --preprocess # solo preprocesador
    python scripts/run_system.py --engine     # solo motor analítico
    python scripts/run_system.py --check      # verificar conexiones
"""

import argparse
import logging
import os
import signal
import sys
import threading

# Añadir el directorio raíz al path para importar los módulos
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from utils.config_loader import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(ROOT, "logs", "system.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("run_system")


def banner():
    print("\n" + "═" * 56)
    print("  Sistema de Detección Temprana de Anomalías - UCI")
    print("  Integración con Moodle  |  Redis Streams  |  Grafana")
    print("═" * 56 + "\n")


def run_capture_only(config: dict):
    """Ejecuta únicamente el subsistema de captura de logs."""
    from capture.log_capture import MoodleLogCapture
    capture = MoodleLogCapture(config)
    logger.info("Iniciando SOLO captura de logs Moodle → Redis Stream")
    _run_with_signal([capture], names=["Capture"])


def run_preprocess_only(config: dict):
    """Ejecuta únicamente el preprocesador."""
    from preprocessing.preprocessor import Preprocessor
    prep = Preprocessor(config)
    logger.info("Iniciando SOLO preprocesador Redis → moodle_timeseries")
    _run_with_signal([prep], names=["Preprocessor"])


def run_engine_only(config: dict):
    """Ejecuta únicamente el motor analítico (requiere stream moodle_timeseries poblado)."""
    sys.path.insert(0, ROOT)
    import importlib
    main_mod = importlib.import_module("main")
    engine = main_mod.AnalyticsEngine(config)
    logger.info("Iniciando SOLO motor analítico")
    _run_with_signal([engine], names=["Analytics"])


def run_full(config: dict):
    """Inicia el sistema completo."""
    # Importar desde main.py del directorio raíz
    sys.path.insert(0, ROOT)
    import importlib
    main_mod = importlib.import_module("main")
    main_mod.main()


def _run_with_signal(subsystems, names):
    """Lanza subsistemas en hilos y registra señal de parada."""
    stop_flag = threading.Event()

    def shutdown(sig, frame):
        logger.info("Señal de parada recibida. Deteniendo subsistemas...")
        stop_flag.set()
        for s in subsystems:
            try:
                s.stop()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    threads = []
    for s, name in zip(subsystems, names):
        t = threading.Thread(target=s.start, name=name, daemon=True)
        t.start()
        logger.info("Subsistema '%s' iniciado.", name)
        threads.append(t)

    logger.info("Presiona Ctrl+C para detener.")
    for t in threads:
        t.join()


def check_connections(config: dict):
    """Delega en check_connections.py."""
    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "check_connections.py"),
         "--config", os.path.join(ROOT, "config", "config.yaml")],
        cwd=ROOT,
    )
    sys.exit(result.returncode)


def main():
    os.makedirs(os.path.join(ROOT, "logs"),          exist_ok=True)
    os.makedirs(os.path.join(ROOT, "models", "saved"), exist_ok=True)

    parser = argparse.ArgumentParser(
        description="Moodle Anomaly Detection – Lanzador del sistema"
    )
    parser.add_argument("--config",      default="config/config.yaml")
    parser.add_argument("--capture",     action="store_true", help="Solo captura de logs")
    parser.add_argument("--preprocess",  action="store_true", help="Solo preprocesador")
    parser.add_argument("--engine",      action="store_true", help="Solo motor analítico")
    parser.add_argument("--check",       action="store_true", help="Verificar conexiones")
    args = parser.parse_args()

    banner()

    config_path = args.config if os.path.isabs(args.config) \
                  else os.path.join(ROOT, args.config)
    config = load_config(config_path)

    if args.check:
        check_connections(config)
    elif args.capture:
        run_capture_only(config)
    elif args.preprocess:
        run_preprocess_only(config)
    elif args.engine:
        run_engine_only(config)
    else:
        logger.info("Iniciando sistema completo...")
        run_full(config)


if __name__ == "__main__":
    main()
