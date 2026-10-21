from __future__ import annotations

from fastapi.testclient import TestClient

TEST_UID = "AB" * 4


def test_api_flow_uses_temp_database_and_mock_runtime(client: TestClient) -> None:
    student = client.post("/api/v1/students", json={"name": "テスト学生", "card_uid": TEST_UID})
    assert student.status_code == 201
    student_id = student.json()["student_id"]

    course = client.post("/api/v1/courses", json={"course_name": "テスト授業"})
    assert course.status_code == 201
    course_id = course.json()["course_id"]

    enrollment = client.post(
        f"/api/v1/courses/{course_id}/enrollments",
        json={"student_id": student_id},
    )
    assert enrollment.status_code == 201

    session = client.post("/api/v1/sessions", json={"course_id": course_id})
    assert session.status_code == 201
    session_id = session.json()["session_id"]

    started = client.post(
        "/api/v1/attendance/runtime/start",
        json={"session_id": session_id, "mode": "MOCK"},
    )
    assert started.status_code == 202
    assert started.json()["state"] == "ATTENDING"

    scan = client.post("/api/v1/attendance/runtime/scan", json={"card_uid": TEST_UID.lower()})
    assert scan.status_code == 200
    assert scan.json()["status"] == "SUCCESS"
    duplicate = client.post("/api/v1/attendance/runtime/scan", json={"card_uid": TEST_UID})
    assert duplicate.json()["status"] == "ALREADY_REGISTERED"

    stopped = client.post("/api/v1/rfid/runtime/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "IDLE"

    closed = client.post(f"/api/v1/sessions/{session_id}/close")
    assert closed.status_code == 200
    report = client.get(f"/api/v1/sessions/{session_id}/attendance")
    assert report.json()[0]["attendance_status"] == "PRESENT"

    events = client.get("/api/v1/rfid/events", params={"after_sequence": 0})
    assert events.status_code == 200
    assert any(event["kind"] == "ATTENDANCE_RESULT" for event in events.json()["events"])


def test_api_validation_does_not_expose_database_details(client: TestClient) -> None:
    response = client.post("/api/v1/students", json={"name": "", "card_uid": "not-uid"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert set(body) == {"code", "message", "request_id", "retryable"}

    invalid_uid = client.post("/api/v1/students", json={"name": "入力確認", "card_uid": "not-uid"})
    assert invalid_uid.status_code == 422
    assert invalid_uid.json()["code"] == "VALIDATION_ERROR"
