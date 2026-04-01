CREATE DATABASE IF NOT EXISTS moodle;
USE moodle;

CREATE TABLE IF NOT EXISTS mdl_logstore_standard_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timecreated INT,
    userid INT,
    courseid INT,
    component VARCHAR(100),
    action VARCHAR(100),
    target VARCHAR(100),
    objectid INT,
    contextlevel INT,
    ip VARCHAR(45)
);

INSERT INTO mdl_logstore_standard_log (timecreated, userid, courseid, component, action, target, objectid, contextlevel, ip) VALUES
(1640995200, 1, 101, 'core', 'viewed', 'course', 101, 50, '192.168.1.10'),
(1640998800, 2, 102, 'mod_forum', 'created', 'discussion', 201, 70, '192.168.1.11'),
(1641002400, 3, 101, 'core', 'loggedin', 'user', 3, 10, '192.168.1.12');
