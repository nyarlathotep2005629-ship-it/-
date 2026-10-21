export type RuntimeState = "IDLE" | "STARTING" | "REGISTERING" | "ATTENDING" | "STOPPING" | "ERROR";
export type RuntimeMode = "MOCK" | "SERIAL";

export interface Student {
  student_id: number;
  name: string;
  card_uid: string;
}

export interface Course {
  course_id: number;
  course_name: string;
}

export interface Enrollment extends Student {}

export interface Session {
  session_id: number;
  course_id: number;
  session_number: number;
  started_at: string;
  closed_at: string | null;
  status?: "OPEN" | "CLOSED";
}

export interface AttendanceRow {
  student_id: number;
  name: string;
  attendance_status: "PRESENT" | "PENDING" | "ABSENT";
  scanned_at: string | null;
}

export interface AttendanceSummary {
  enrolled_count: number;
  present_count: number;
  pending_count: number;
  absent_count: number;
}

export interface SerialPort {
  device: string;
  description: string;
  hwid: string;
}

export interface RuntimeStatus {
  server_instance_id: string;
  state: RuntimeState;
  operation: string;
  operation_id: string | null;
  session_id: number | null;
  mode: RuntimeMode | null;
  port: string | null;
  updated_at: string;
  last_error: string | null;
  last_completed_operation_id: string | null;
  last_terminal_event_kind: string | null;
}

export interface RuntimeEvent {
  sequence: number;
  kind: string;
  created_at: string;
  state: RuntimeState;
  operation_id: string | null;
  payload: Record<string, unknown>;
}

export interface EventBatch {
  server_instance_id: string;
  first_sequence: number;
  last_sequence: number;
  next_after_sequence: number;
  reset_required: boolean;
  events: RuntimeEvent[];
}

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id: string;
  retryable: boolean;
}
