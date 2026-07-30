"""
real_time_extractor.py
====================
Extractor en tiempo real usando CDC (Change Data Capture) para Moodle.

Métodos disponibles:
1. MySQL Triggers + Redis Pub/Sub (recomendado para producción)
2. Polling con timestamp tracking (fallback)
3. Debezium CDC connector (para infraestructura completa)

Configurar en config/config.yaml bajo la sección "moodle" -> "extraction_mode"
"""

import logging
import threading
import time
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class BaseExtractor(ABC):
    """Clase base para extractores."""
    
    @abstractmethod
    def start(self, callback: Callable):
        """Inicia la extracción con callback para nuevos datos."""
        pass
    
    @abstractmethod
    def stop(self):
        """Detiene la extracción."""
        pass


class PollingExtractor(BaseExtractor):
    """
    Extractor por polling tradicional.
    Mantiene tracking del último timestamp procesado.
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.poll_interval = config.get("moodle", {}).get("poll_interval_seconds", 10)
        self.last_timestamp_key = "moodle_extractor:last_timestamp"
        self._running = False
        self._thread = None
        self._redis = None
        
    def _get_last_timestamp(self) -> Optional[int]:
        """Obtiene el último timestamp procesado."""
        if self._redis:
            ts = self._redis.get(self.last_timestamp_key)
            return int(ts) if ts else None
        return None
    
    def _set_last_timestamp(self, ts: int):
        """Guarda el último timestamp procesado."""
        if self._redis:
            self._redis.set(self.last_timestamp_key, str(ts))
    
    def start(self, callback: Callable):
        """Inicia el polling."""
        import redis
        self._redis = redis.Redis(
            host=self.config["redis"]["host"],
            port=self.config["redis"]["port"]
        )
        
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, args=(callback,))
        self._thread.daemon = True
        self._thread.start()
        logger.info(f"PollingExtractor started (interval: {self.poll_interval}s)")
    
    def _poll_loop(self, callback: Callable):
        """Loop de polling."""
        while self._running:
            try:
                self._poll_once(callback)
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
            time.sleep(self.poll_interval)
    
    def _poll_once(self, callback: Callable):
        """Ejecuta un ciclo de polling."""
        import mysql.connector
        
        last_ts = self._get_last_timestamp()
        
        conn = mysql.connector.connect(
            host=self.config["moodle"]["host"],
            port=self.config["moodle"]["port"],
            database=self.config["moodle"]["database"],
            user=self.config["moodle"]["user"],
            password=self.config["moodle"]["password"]
        )
        
        cursor = conn.cursor(dictionary=True)
        
        if last_ts:
            query = f"""
            SELECT * FROM {self.config["moodle"]["log_table"]}
            WHERE UNIX_TIMESTAMP(timecreated) > %s
            ORDER BY timecreated ASC
            LIMIT 1000
            """
            cursor.execute(query, (last_ts,))
        else:
            query = f"""
            SELECT * FROM {self.config["moodle"]["log_table"]}
            ORDER BY timecreated DESC
            LIMIT 100
            """
            cursor.execute(query)
        
        rows = cursor.fetchall()
        
        if rows:
            # Procesar de más antiguo a más nuevo
            for row in rows:
                ts = int(row.get("timecreated", 0).timestamp() if hasattr(row.get("timecreated"), "timestamp") else row["timecreated"])
                
                # Extraer features
                event = {
                    "timestamp": ts,
                    "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "user_id": row.get("userid"),
                    "action": row.get("action"),
                    "target": row.get("target"),
                    "course_id": row.get("courseid"),
                    "ip_address": row.get("ip_address"),
                }
                
                callback(event)
            
            # Actualizar último timestamp
            latest_ts = rows[-1].get("timecreated")
            if latest_ts:
                if hasattr(latest_ts, "timestamp"):
                    self._set_last_timestamp(int(latest_ts.timestamp()))
                else:
                    self._set_last_timestamp(int(latest_ts))
        
        cursor.close()
        conn.close()
    
    def stop(self):
        """Detiene el polling."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)


class CDCTriggerExtractor(BaseExtractor):
    """
    Extractor usando MySQL Triggers + Redis Pub/Sub.
    Necesita que se creen triggers en MySQL (ver scripts/setup_cdc_triggers.sql)
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.channel = "moodle:cdc:logs"
        self._running = False
        self._thread = None
        self._pubsub = None
        self._redis = None
    
    def start(self, callback: Callable):
        """Inicia el suscriptor CDC."""
        import redis
        
        self._redis = redis.Redis(
            host=self.config["redis"]["host"],
            port=self.config["redis"]["port"]
        )
        
        pubsub = self._redis.pubsub()
        pubsub.subscribe(self.channel)
        self._pubsub = pubsub
        
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, args=(callback,))
        self._thread.daemon = True
        self._thread.start()
        
        logger.info(f"CDCTriggerExtractor started, subscribed to {self.channel}")
    
    def _listen_loop(self, callback: Callable):
        """Loop de escucha de mensajes CDC."""
        for message in self._pubsub.listen():
            if not self._running:
                break
            if message["type"] == "message":
                try:
                    import json
                    data = json.loads(message["data"])
                    callback(data)
                except Exception as e:
                    logger.error(f"Error processing CDC message: {e}")
    
    def stop(self):
        """Detiene el suscriptor."""
        self._running = False
        if self._pubsub:
            self._pubsub.unsubscribe()
        if self._thread:
            self._thread.join(timeout=5)


class RealTimeExtractor:
    """
    Facade para el extractor en tiempo real.
    Selecciona el método de extracción según configuración.
    """
    
    def __init__(self, config: dict):
        self.config = config
        self._extractor: Optional[BaseExtractor] = None
        self._setup_extractor()
    
    def _setup_extractor(self):
        """Configura el extractor según el modo."""
        mode = self.config.get("moodle", {}).get("extraction_mode", "polling")
        
        if mode == "cdc_triggers":
            self._extractor = CDCTriggerExtractor(self.config)
            logger.info("Using CDC Trigger extraction mode")
        elif mode == "polling":
            self._extractor = PollingExtractor(self.config)
            logger.info("Using Polling extraction mode")
        else:
            logger.warning(f"Unknown extraction mode '{mode}', defaulting to polling")
            self._extractor = PollingExtractor(self.config)
    
    def start(self, callback: Callable):
        """Inicia la extracción."""
        if self._extractor:
            self._extractor.start(callback)
    
    def stop(self):
        """Detiene la extracción."""
        if self._extractor:
            self._extractor.stop()


# =============================================================================
# TRIGGER SQL SCRIPT (para MySQL)
# =============================================================================

CDC_TRIGGER_SQL = """
-- =====================================================
-- CDC Triggers para Moodle Log Mining en tiempo real
-- =====================================================
-- Ejecutar en la base de datos Moodle de MySQL
-- =====================================================

-- Tabla auxiliar para publicar cambios
CREATE TABLE IF NOT EXISTS moodle_cdc_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_id INT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_data JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed BOOLEAN DEFAULT FALSE,
    INDEX idx_processed (processed),
    INDEX idx_created (created_at)
);

-- Trigger AFTER INSERT para nuevas entradas
DELIMITER //

CREATE TRIGGER moodle_log_after_insert
AFTER INSERT ON mdl_logstore_standard_log
FOR EACH ROW
BEGIN
    INSERT INTO moodle_cdc_log (event_id, event_type, event_data)
    VALUES (
        NEW.id,
        'INSERT',
        JSON_OBJECT(
            'id', NEW.id,
            'userid', NEW.userid,
            'action', NEW.action,
            'target', NEW.target,
            'courseid', NEW.courseid,
            'timecreated', NEW.timecreated
        )
    );
END//

DELIMITER ;

-- Stored procedure para procesar cambios pendientes
DELIMITER //

CREATE PROCEDURE process_moodle_cdc(IN batch_size INT)
BEGIN
    DECLARE done BOOLEAN DEFAULT FALSE;
    DECLARE cur_id INT;
    DECLARE cur_data JSON;
    
    DECLARE cur CURSOR FOR 
        SELECT id, event_data FROM moodle_cdc_log 
        WHERE processed = FALSE ORDER BY created_at ASC LIMIT batch_size;
    
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;
    
    OPEN cur;
    
    read_loop: LOOP
        FETCH cur INTO cur_id, cur_data;
        IF done THEN
            LEAVE read_loop;
        END IF;
        
        -- Aquí publicar a Redis (ejemplo básico)
        -- En producción, usar un client MySQL que publique directamente
        SELECT SLEEP(0.01); -- Yield para no bloquear
        
        UPDATE moodle_cdc_log SET processed = TRUE WHERE id = cur_id;
    END LOOP;
    
    CLOSE cur;
END//

DELIMITER ;
"""

CDC_SETUP_SCRIPT = """
-- Script completo de setup para CDC
-- Guardar como setup_cdc_triggers.sql y ejecutar:
-- mysql -u root -p moodle < setup_cdc_triggers.sql

USE moodle;

-- Crear tabla CDC
CREATE TABLE IF NOT EXISTS moodle_cdc_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_id INT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_data JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed BOOLEAN DEFAULT FALSE,
    INDEX idx_processed (processed),
    INDEX idx_created (created_at)
);

-- Eliminar triggers existentes si hay
DROP TRIGGER IF EXISTS moodle_log_after_insert;

-- Crear trigger
DELIMITER //

CREATE TRIGGER moodle_log_after_insert
AFTER INSERT ON mdl_logstore_standard_log
FOR EACH ROW
BEGIN
    INSERT INTO moodle_cdc_log (event_id, event_type, event_data)
    VALUES (
        NEW.id,
        'INSERT',
        JSON_OBJECT(
            'id', NEW.id,
            'userid', NEW.userid,
            'action', NEW.action,
            'target', NEW.target,
            'courseid', NEW.courseid,
            'timecreated', NEW.timecreated,
            'ip_address', NEW.ip_address
        )
    );
END//

DELIMITER ;
"""

if __name__ == "__main__":
    import yaml
    import os
    
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    def on_new_event(event):
        print(f"Nuevo evento: {event}")
    
    extractor = RealTimeExtractor(config)
    extractor.start(on_new_event)
    
    print("Extractor iniciado. Presiona Ctrl+C para detener.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\\nDeteniendo extractor...")
        extractor.stop()