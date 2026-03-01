"""
scripts/check_connections.py
============================
Script de diagnóstico: verifica que todos los servicios externos estén
accesibles antes de iniciar el sistema principal.

Uso:
    python scripts/check_connections.py
    python scripts/check_connections.py --config config/config.yaml
"""

import argparse
import sys
import time

# Añadir el directorio raíz al path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config_loader import load_config

# ── Colores ANSI ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):   print(f"  {GREEN}✔{RESET}  {msg}")
def fail(msg): print(f"  {RED}✘{RESET}  {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RESET}  {msg}")
def info(msg): print(f"  {BLUE}ℹ{RESET}  {msg}")


# ── Verificaciones individuales ───────────────────────────────────────────────

def check_redis(cfg: dict) -> bool:
    """Verifica conectividad con Redis y la existencia del stream."""
    try:
        import redis
        r = redis.Redis(
            host=cfg["redis"]["host"],
            port=cfg["redis"]["port"],
            socket_connect_timeout=5,
            decode_responses=True,
        )
        pong = r.ping()
        if pong:
            ok(f"Redis reachable at {cfg['redis']['host']}:{cfg['redis']['port']}")
        # Verificar version
        info_data = r.info("server")
        info(f"Redis version: {info_data.get('redis_version', 'unknown')}")
        # Listar streams existentes si los hay
        stream = cfg["redis"]["stream_name"]
        try:
            length = r.xlen(stream)
            info(f"Stream '{stream}' existe con {length} mensajes")
        except Exception:
            info(f"Stream '{stream}' no existe aún (se creará al iniciar)")
        r.close()
        return True
    except Exception as exc:
        fail(f"Redis: {exc}")
        return False


def check_moodle_db(cfg: dict) -> bool:
    """Verifica la conexión a la base de datos MySQL de Moodle."""
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host=cfg["moodle"]["host"],
            port=cfg["moodle"]["port"],
            database=cfg["moodle"]["database"],
            user=cfg["moodle"]["user"],
            password=cfg["moodle"]["password"],
            connection_timeout=10,
        )
        cursor = conn.cursor()
        table = cfg["moodle"]["log_table"]
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        ok(f"Moodle DB reachable at {cfg['moodle']['host']}:{cfg['moodle']['port']}")
        info(f"Tabla '{table}': {count:,} registros totales")
        # Verificar logs recientes (últimas 24h)
        cursor.execute(
            f"SELECT COUNT(*) FROM {table} WHERE timecreated > UNIX_TIMESTAMP(NOW() - INTERVAL 24 HOUR)"
        )
        recent = cursor.fetchone()[0]
        info(f"Logs en las últimas 24h: {recent:,}")
        cursor.close()
        conn.close()
        return True
    except Exception as exc:
        fail(f"Moodle DB: {exc}")
        return False


def check_influxdb(cfg: dict) -> bool:
    """Verifica la conexión al servidor InfluxDB."""
    try:
        from influxdb_client import InfluxDBClient
        client = InfluxDBClient(
            url=cfg["influxdb"]["url"],
            token=cfg["influxdb"]["token"],
            org=cfg["influxdb"]["org"],
            timeout=10_000,
        )
        health = client.health()
        if health.status == "pass":
            ok(f"InfluxDB reachable at {cfg['influxdb']['url']}")
            info(f"InfluxDB version: {health.version}")
        else:
            warn(f"InfluxDB health status: {health.status}")

        # Verificar bucket
        buckets_api = client.buckets_api()
        bucket = buckets_api.find_bucket_by_name(cfg["influxdb"]["bucket"])
        if bucket:
            info(f"Bucket '{cfg['influxdb']['bucket']}' encontrado (id: {bucket.id})")
        else:
            warn(f"Bucket '{cfg['influxdb']['bucket']}' no encontrado. "
                 "Ejecuta: python scripts/setup_influxdb.py")
        client.close()
        return True
    except Exception as exc:
        fail(f"InfluxDB: {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Comprueba las conexiones del sistema")
    parser.add_argument("--config", default="config/config.yaml",
                        help="Ruta al archivo de configuración")
    args = parser.parse_args()

    print(f"\n{BOLD}═══════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}  Verificación de conexiones - Moodle Anomaly System{RESET}")
    print(f"{BOLD}═══════════════════════════════════════════════════{RESET}\n")

    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        fail(f"Archivo de configuración no encontrado: {args.config}")
        sys.exit(1)

    results = {}

    print(f"{BOLD}[1/3] Redis (broker de mensajería){RESET}")
    results["redis"] = check_redis(cfg)

    print(f"\n{BOLD}[2/3] Moodle MySQL Database{RESET}")
    results["moodle"] = check_moodle_db(cfg)

    print(f"\n{BOLD}[3/3] InfluxDB (series temporales){RESET}")
    results["influxdb"] = check_influxdb(cfg)

    # Resumen
    total  = len(results)
    passed = sum(results.values())
    failed = total - passed

    print(f"\n{BOLD}{'═'*51}{RESET}")
    print(f"{BOLD}  Resultado: {passed}/{total} servicios OK{RESET}")
    if failed:
        print(f"  {RED}Servicios con error: "
              f"{', '.join(k for k, v in results.items() if not v)}{RESET}")
        print(f"\n  Revisa la configuración en {args.config}")
        sys.exit(1)
    else:
        print(f"  {GREEN}Sistema listo para iniciar.{RESET}")
        print(f"  Ejecuta: {BOLD}python main.py{RESET}")

    print(f"{BOLD}{'═'*51}{RESET}\n")


if __name__ == "__main__":
    main()
