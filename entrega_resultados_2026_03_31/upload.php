<?php
// upload.php
$target_dir = __DIR__ . "/uploads/";
if (!file_exists($target_dir)) {
    mkdir($target_dir, 0777, true);
}
$target_file = $target_dir . basename($_FILES["fileInput"]["name"]);
if (isset($_FILES["fileInput"])) {
    if (move_uploaded_file($_FILES["fileInput"]["tmp_name"], $target_file)) {
        echo "<div style='font-family:Segoe UI,Arial,sans-serif;padding:2rem;text-align:center;'><h2 style='color:#0078d4;'>Archivo subido correctamente.</h2><p>Nombre: <b>" . htmlspecialchars(basename($_FILES["fileInput"]["name"])) . "</b></p><a href='upload.html' style='color:#0078d4;text-decoration:underline;'>Subir otro archivo</a></div>";
    } else {
        echo "<div style='font-family:Segoe UI,Arial,sans-serif;padding:2rem;text-align:center;'><h2 style='color:#d40000;'>Error al subir el archivo.</h2><a href='upload.html' style='color:#0078d4;text-decoration:underline;'>Intentar de nuevo</a></div>";
    }
} else {
    echo "<div style='font-family:Segoe UI,Arial,sans-serif;padding:2rem;text-align:center;'><h2 style='color:#d40000;'>No se recibió ningún archivo.</h2><a href='upload.html' style='color:#0078d4;text-decoration:underline;'>Intentar de nuevo</a></div>";
}
?>
