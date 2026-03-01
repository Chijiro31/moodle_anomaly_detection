"""
scripts/setup_influxdb.py
=========================
Configuración inicial de InfluxDB: crea la organización, el bucket
y genera un token de API listo para usar en config.yaml.

Úsalo UNA VEZ después de arrancar InfluxDB por primera vez.

Uso:
    python scripts/setup_influxdb.py
    python scripts/setup_influxdb.py --url http://localhost:8086 --token mi-token-admin
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config_loader import load_config


def setup_influxdb(cfg: dict, admin_token: str | None = None):
    from influxdb_client import InfluxDBClient, BucketRetentionRules
    from influxdb_client.client.exceptions import InfluxDBError

    influx_cfg  = cfg["influxdb"]
    url         = influx_cfg["url"]
    org_name    = influx_cfg["org"]
    bucket_name = influx_cfg["bucket"]
    # Si se pasa un token de admin diferente, úsalo para el setup
    token = admin_token or influx_cfg["token"]

    print(f"\n  Conectando a InfluxDB en {url} ...")
    client = InfluxDBClient(url=url, token=token, org=org_name)

    # 1. Verificar organización
    orgs_api = client.organizations_api()
    orgs     = orgs_api.find_organizations(org=org_name)
    if orgs:
        org = orgs[0]
        print(f"  ✔ Organización '{org_name}' ya existe (id: {org.id})")
    else:
        org = orgs_api.create_organization(name=org_name)
        print(f"  ✔ Organización '{org_name}' creada (id: {org.id})")

    # 2. Verificar / crear bucket
    buckets_api = client.buckets_api()
    bucket      = buckets_api.find_bucket_by_name(bucket_name)
    if bucket:
        print(f"  ✔ Bucket '{bucket_name}' ya existe (id: {bucket.id})")
    else:
        retention = BucketRetentionRules(type="expire", every_seconds=30 * 24 * 3600)  # 30 días
        bucket    = buckets_api.create_bucket(
            bucket_name=bucket_name,
            retention_rules=retention,
            org_id=org.id,
        )
        print(f"  ✔ Bucket '{bucket_name}' creado con retención de 30 días")

    # 3. Crear token de acceso con permisos de escritura/lectura sobre el bucket
    auth_api   = client.authorizations_api()
    from influxdb_client import Permission, PermissionResource, Authorization
    perms = [
        Permission(
            action="read",
            resource=PermissionResource(type="buckets", id=bucket.id, org_id=org.id),
        ),
        Permission(
            action="write",
            resource=PermissionResource(type="buckets", id=bucket.id, org_id=org.id),
        ),
    ]
    auth = auth_api.create_authorization(
        org_id=org.id,
        permissions=perms,
        description="Moodle Anomaly Detection API token",
    )
    print(f"\n  ✔ Token API generado:")
    print(f"    {auth.token}\n")
    print("  ─────────────────────────────────────────────────")
    print("  Actualiza config/config.yaml con este token:")
    print(f"    influxdb:")
    print(f"      token: \"{auth.token}\"")
    print("  ─────────────────────────────────────────────────\n")

    client.close()
    return auth.token


def main():
    parser = argparse.ArgumentParser(description="Setup inicial de InfluxDB")
    parser.add_argument("--config",       default="config/config.yaml",
                        help="Ruta al archivo de configuración")
    parser.add_argument("--admin-token",  default=None,
                        help="Token de administrador de InfluxDB (overrides config)")
    args = parser.parse_args()

    print("\n══════════════════════════════════════════")
    print("  Setup InfluxDB - Moodle Anomaly System  ")
    print("══════════════════════════════════════════")

    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        print(f"  ✘ Archivo de configuración no encontrado: {args.config}")
        sys.exit(1)

    try:
        setup_influxdb(cfg, admin_token=args.admin_token)
    except Exception as exc:
        print(f"  ✘ Error durante el setup: {exc}")
        raise


if __name__ == "__main__":
    main()
