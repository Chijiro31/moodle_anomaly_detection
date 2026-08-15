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
    batch_bucket_name = influx_cfg.get("batch_bucket", "moodle_batch_analysis")
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

    # 2. Verificar / crear buckets (monitoreo en vivo + analisis de datasets bajo demanda)
    buckets_api = client.buckets_api()

    def ensure_bucket(name: str, retention_days: int = 30):
        b = buckets_api.find_bucket_by_name(name)
        if b:
            print(f"  ✔ Bucket '{name}' ya existe (id: {b.id})")
            return b
        retention = BucketRetentionRules(type="expire", every_seconds=retention_days * 24 * 3600)
        b = buckets_api.create_bucket(bucket_name=name, retention_rules=retention, org_id=org.id)
        print(f"  ✔ Bucket '{name}' creado con retención de {retention_days} días")
        return b

    bucket = ensure_bucket(bucket_name, retention_days=30)
    # El bucket de analisis por lote usa retencion mas larga (o indefinida):
    # los datasets que se analizan pueden tener fechas historicas (2019, 2023...)
    # que un vencimiento corto borraria antes de que el usuario los revise.
    batch_bucket = ensure_bucket(batch_bucket_name, retention_days=3650)

    # 3. Crear token de acceso con permisos de escritura/lectura sobre ambos buckets
    auth_api   = client.authorizations_api()
    from influxdb_client import Permission, PermissionResource, Authorization
    perms = []
    for b in (bucket, batch_bucket):
        perms.append(Permission(
            action="read",
            resource=PermissionResource(type="buckets", id=b.id, org_id=org.id),
        ))
        perms.append(Permission(
            action="write",
            resource=PermissionResource(type="buckets", id=b.id, org_id=org.id),
        ))
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
