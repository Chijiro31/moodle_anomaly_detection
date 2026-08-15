"""
Subsistema de preprocesamiento (RF2).
Lee eventos crudos del Redis Stream, los limpia, normaliza y agrega
en ventanas temporales de 60 segundos, construyendo series temporales
con las siguientes features:
  - request_count     : numero total de peticiones en la ventana
  - unique_users      : usuarios unicos activos
  - error_count       : acciones que representan errores (failed, denied...)
  - course_count      : cursos distintos accedidos
"""

import time
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone

import redis

from utils.config_loader import load_config

logger = logging.getLogger(__name__)

ERROR_KEYWORDS = {"failed", "denied", "error", "invalid", "exception"}


class Preprocessor:
    """
    Agrega eventos del stream en ventanas temporales y publica
    vectores de features en un segundo stream ('moodle_timeseries').
    """

    def __init__(self, config: dict):
        self.cfg = config
        self.redis_cfg = config["redis"]
        self.prep_cfg = config["preprocessing"]
        self.window_sec = self.prep_cfg.get("aggregation_window_seconds", 60)
        self._stop_event = threading.Event()

        self.redis_client = redis.Redis(
            host=self.redis_cfg["host"],
            port=self.redis_cfg["port"],
            decode_responses=True,
        )
        self.stream_in  = self.redis_cfg["stream_name"]
        self.stream_out = "moodle_timeseries"
        self.group      = self.redis_cfg["consumer_group"]
        self.consumer   = "preprocessor"

        # Asegurarse de que el grupo existe
        try:
            self.redis_client.xgroup_create(
                self.stream_in, self.group, id="0", mkstream=True
            )
        except redis.exceptions.ResponseError:
            pass

        self._window_data: dict = defaultdict(lambda: {
            "request_count": 0,
            "unique_users": set(),
            "error_count": 0,
            "course_count": set(),
        })

    # ------------------------------------------------------------------
    # Bucle principal
    # ------------------------------------------------------------------

    def start(self):
        logger.info("Preprocesador iniciado. Ventana: %ds", self.window_sec)
        last_flush = time.time()
        while not self._stop_event.is_set():
            self._consume_batch()
            if time.time() - last_flush >= self.window_sec:
                self._flush_window()
                last_flush = time.time()
            time.sleep(1)

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Consumo y flush
    # ------------------------------------------------------------------

    def _consume_batch(self):
        entries = self.redis_client.xreadgroup(
            self.group,
            self.consumer,
            {self.stream_in: ">"},
            count=500,
            block=1000,
        )
        if not entries:
            return
        ids_to_ack = []
        for _, messages in entries:
            for msg_id, fields in messages:
                self._aggregate(fields)
                ids_to_ack.append(msg_id)
        if ids_to_ack:
            self.redis_client.xack(self.stream_in, self.group, *ids_to_ack)

    def _aggregate(self, fields: dict):
        """Clasifica y acumula un evento en la ventana actual."""
        now_bucket = int(time.time() // self.window_sec) * self.window_sec
        bucket = self._window_data[now_bucket]
        bucket["request_count"] += 1

        user_id = fields.get("userid", "0")
        if user_id and user_id != "0":
            bucket["unique_users"].add(user_id)

        action = fields.get("action", "").lower()
        if any(kw in action for kw in ERROR_KEYWORDS):
            bucket["error_count"] += 1

        course_id = fields.get("courseid", "0")
        if course_id and course_id != "0":
            bucket["course_count"].add(course_id)

    def _flush_window(self):
        """Publica las ventanas cerradas como series temporales."""
        now_bucket = int(time.time() // self.window_sec) * self.window_sec
        closed_buckets = [ts for ts in self._window_data if ts < now_bucket]

        for ts in closed_buckets:
            data = self._window_data.pop(ts)
            point = {
                "timestamp":    str(ts),
                "request_count":str(data["request_count"]),
                "unique_users": str(len(data["unique_users"])),
                "error_count":  str(data["error_count"]),
                "course_count": str(len(data["course_count"])),
            }
            self.redis_client.xadd(
                self.stream_out, point, maxlen=50000, approximate=True
            )
            logger.debug(
                "Ventana %s publicada: %s", datetime.fromtimestamp(ts).isoformat(), point
            )
