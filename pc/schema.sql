PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS students (
    student_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
        CHECK (length(trim(name)) > 0),
    card_uid TEXT NOT NULL UNIQUE
        CHECK (
            length(card_uid) IN (8, 14, 20)
            AND card_uid NOT GLOB '*[^0-9A-F]*'
        )
);

CREATE TABLE IF NOT EXISTS courses (
    course_id INTEGER PRIMARY KEY,
    course_name TEXT NOT NULL
        CHECK (length(trim(course_name)) > 0)
);

CREATE TABLE IF NOT EXISTS enrollments (
    student_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,

    PRIMARY KEY (student_id, course_id),

    FOREIGN KEY (student_id)
        REFERENCES students(student_id),

    FOREIGN KEY (course_id)
        REFERENCES courses(course_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL,
    session_number INTEGER NOT NULL
        CHECK (session_number >= 1),
    started_at TEXT NOT NULL,
    closed_at TEXT,

    UNIQUE (course_id, session_number),

    CHECK (
        closed_at IS NULL
        OR closed_at >= started_at
    ),

    FOREIGN KEY (course_id)
        REFERENCES courses(course_id)
);

CREATE TABLE IF NOT EXISTS attendance (
    session_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    scanned_at TEXT NOT NULL,

    PRIMARY KEY (session_id, student_id),

    FOREIGN KEY (session_id)
        REFERENCES sessions(session_id),

    FOREIGN KEY (student_id)
        REFERENCES students(student_id)
);

CREATE TRIGGER IF NOT EXISTS trg_attendance_requires_enrollment
BEFORE INSERT ON attendance
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1
    FROM sessions AS se
    JOIN enrollments AS e
        ON e.course_id = se.course_id
       AND e.student_id = NEW.student_id
    WHERE se.session_id = NEW.session_id
)
BEGIN
    SELECT RAISE(ABORT, 'student_not_enrolled');
END;

CREATE UNIQUE INDEX IF NOT EXISTS uq_one_open_session
    ON sessions ((1))
    WHERE closed_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_enrollments_course
    ON enrollments (course_id, student_id);
