-- moodle_mock_bulk.sql
-- Inserta 1000 logs de ejemplo en la tabla mdl_logstore_standard_log

INSERT INTO mdl_logstore_standard_log (
    id, timecreated, userid, courseid, component, action, target, objectid, contextlevel, ip
)
VALUES
(1001,  1768204800,  10,  2, 'core', 'viewed', 'course',  2,  50, '192.168.1.10'),
(1002,  1768204860,  11,  2, 'core', 'viewed', 'course',  2,  50, '192.168.1.11'),
(1003,  1768204920,  12,  3, 'mod_forum', 'created', 'discussion',  5,  70, '192.168.1.12'),
(1004,  1768204980,  13,  2, 'mod_quiz', 'attempted', 'quiz',  7,  60, '192.168.1.13'),
(1005,  1768205040,  14,  4, 'core', 'loggedin', 'user',  14,  50, '192.168.1.14'),
(1006,  1768205100,  15,  2, 'mod_forum', 'viewed', 'discussion',  5,  70, '192.168.1.15'),
(1007,  1768205160,  16,  3, 'core', 'viewed', 'course',  3,  50, '192.168.1.16'),
(1008,  1768205220,  17,  4, 'mod_quiz', 'attempted', 'quiz',  8,  60, '192.168.1.17'),
(1009,  1768205280,  18,  2, 'core', 'loggedout', 'user',  18,  50, '192.168.1.18'),
(1010, 1768205340,  19,  3, 'mod_forum', 'created', 'post',  9,  70, '192.168.1.19');
