# Instrucciones para montar el sistema de carga de archivos localmente

1. Asegúrate de tener PHP instalado en tu sistema (puedes usar WSL, XAMPP, WAMP, o PHP embebido).
2. Coloca los archivos `upload.html` y `upload.php` en la carpeta `moodle_anomaly_detection`.
3. La carpeta `uploads` ya está creada y es donde se guardarán los archivos subidos.
4. Abre una terminal y navega a la carpeta `moodle_anomaly_detection`.
5. Ejecuta el siguiente comando para iniciar un servidor PHP local:

    php -S localhost:8080

6. Abre tu navegador y accede a:

    http://localhost:8080/upload.html

7. Sube archivos usando el formulario. Los archivos aparecerán en la carpeta `uploads`.

---

## Notas
- Si usas WSL o Linux, puedes servir la carpeta con Nginx o Apache apuntando el root a `moodle_anomaly_detection`.
- Si usas Nginx, asegúrate de tener PHP-FPM configurado y que la ruta de los scripts PHP esté habilitada.
- Si necesitas ayuda para montar en Nginx o Apache, indícalo y te doy la configuración específica.
