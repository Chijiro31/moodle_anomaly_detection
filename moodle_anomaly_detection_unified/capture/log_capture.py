"""
Subsistema de captura de logs de Moodle.
Conecta a la base de datos MySQL de Moodle, lee la tabla mdl_logstore_standard_log
de forma continua y publica los nuevos registros en un Redis Stream.
"""

import time
import json
import logging
import threading
from datetime import datetime, timezone

import mysql.connector
import redis

from utils.config_loader import load_config

logger = logging.getLogger(__name__)


class MoodleLogCapture:
    """
    Captura los logs generados por el servidor Moodle en tiempo real.
    Consulta periodicamente la base de datos y publica nuevos eventos
    en un Redis Stream (RF1).
    """

    # Persistido en Redis para no perder el cursor de captura ante
    # reconexiones o reinicios del proceso (ver _load_last_id).
    LAST_ID_KEY = "moodle_capture:last_id"

    def __init__(self, config: dict):
        self.cfg = config
        self.moodle_cfg = config["moodle"]
        self.redis_cfg = config["redis"]
        self.poll_interval = self.moodle_cfg.get("poll_interval_seconds", 10)
        self._stop_event = threading.Event()
        self._last_id = 0          # ultimo ID procesado de la tabla de logs

        self.redis_client = self._connect_redis()
        self.db_conn = self._connect_moodle()
        self._last_id = self._load_last_id(self.db_conn)

    # ------------------------------------------------------------------
    # Conexiones
    # ------------------------------------------------------------------

    def _connect_redis(self) -> redis.Redis:
        r = redis.Redis(
            host=self.redis_cfg["host"],
            port=self.redis_cfg["port"],
            decode_responses=True,
        )
        # Crear grupo de consumo si no existe
        stream = self.redis_cfg["stream_name"]
        try:
            r.xgroup_create(stream, self.redis_cfg["consumer_group"], id="0", mkstream=True)
            logger.info("Grupo de consumo '%s' creado.", self.redis_cfg["consumer_group"])
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                logger.info("Grupo de consumo ya existe.")
            else:
                raise
        return r

    def _connect_moodle(self) -> mysql.connector.connection.MySQLConnection:
        conn = mysql.connector.connect(
            host=self.moodle_cfg["host"],
            port=self.moodle_cfg["port"],
            database=self.moodle_cfg["database"],
            user=self.moodle_cfg["user"],
            password=self.moodle_cfg["password"],
            autocommit=True,
            connection_timeout=30,
        )
        logger.info("Conexion a Moodle DB establecida.")
        return conn

    def _load_last_id(self, conn) -> int:
        """
        Recupera el ultimo ID de log procesado desde Redis, para que una
        reconexion (o un reinicio del proceso) retome exactamente donde se
        quedo, en vez de saltar al MAX(id) actual de Moodle y perder de
        forma silenciosa los eventos generados durante la interrupcion.
        Solo si no hay estado previo (primer arranque del sistema) se usa
        MAX(id) como punto de partida, para no reprocesar todo el
        historial de logs existente.
        """
        saved = self.redis_client.get(self.LAST_ID_KEY)
        if saved is not None:
            last_id = int(saved)
            logger.info("Ultimo ID de log recuperado de Redis: %d", last_id)
            return last_id

        last_id = self._get_max_log_id(conn)
        # Se persiste de inmediato: si el proceso se reinicia antes de que
        # ocurra un primer fetch con filas nuevas (unico otro punto donde
        # se persiste), este valor inicial no debe perderse.
        self.redis_client.set(self.LAST_ID_KEY, str(last_id))
        logger.info("Sin estado previo en Redis. Ultimo ID de log al inicio: %d", last_id)
        return last_id

    def _get_max_log_id(self, conn) -> int:
        cursor = conn.cursor()
        table = self.moodle_cfg["log_table"]
        cursor.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
        result = cursor.fetchone()[0]
        cursor.close()
        return int(result)

    # ------------------------------------------------------------------
    # Bucle principal de captura
    # ------------------------------------------------------------------

    def start(self):
        logger.info("Subsistema de captura iniciado. Intervalo: %ds", self.poll_interval)
        while not self._stop_event.is_set():
            try:
                self._fetch_and_publish()
            except mysql.connector.errors.OperationalError:
                logger.warning("Conexion perdida con Moodle DB. Reconectando...")
                self.db_conn = self._connect_moodle()
            except Exception as exc:
                logger.error("Error en captura: %s", exc)
            time.sleep(self.poll_interval)

    def stop(self):
        self._stop_event.set()
        logger.info("Subsistema de captura detenido.")

    def _fetch_and_publish(self):
        """Consulta registros nuevos y los publica en el Redis Stream."""
        table = self.moodle_cfg["log_table"]
        query = f"""
            SELECT
                id, timecreated, userid, courseid, component,
                action, target, objectid, contextlevel, ip
            FROM {table}
            WHERE id > %s
            ORDER BY id ASC
            LIMIT 5000
        """
        cursor = self.db_conn.cursor(dictionary=True)
        cursor.execute(query, (self._last_id,))
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            return

        stream = self.redis_cfg["stream_name"]
        max_len = self.redis_cfg.get("max_len", 100000)
        pipeline = self.redis_client.pipeline()

        for row in rows:
            event = {
                "id":           str(row["id"]),
                "timecreated":  str(row["timecreated"]),
                "userid":       str(row["userid"]),
                "courseid":     str(row["courseid"] or 0),
                "component":    row["component"] or "",
                "action":       row["action"] or "",
                "target":       row["target"] or "",
                "objectid":     str(row["objectid"] or 0),
                "contextlevel": str(row["contextlevel"] or 0),
                "ip":           row["ip"] or "",
                "captured_at":  str(int(datetime.now(timezone.utc).timestamp())),
            }
            pipeline.xadd(stream, event, maxlen=max_len, approximate=True)
            self._last_id = max(self._last_id, row["id"])

        pipeline.execute()
        self.redis_client.set(self.LAST_ID_KEY, str(self._last_id))
        logger.info(
            "%d eventos publicados en el stream '%s'. Ultimo ID: %d",
            len(rows), stream, self._last_id,
        )
