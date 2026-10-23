import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiRequestError } from "./api/client";
import type { AttendanceRow, AttendanceSummary, Course, RuntimeEvent, RuntimeMode, RuntimeStatus, SerialPort, Session, Student } from "./api/types";
import { useRuntimePolling } from "./hooks/useRuntimePolling";

type Page = "dashboard" | "attendance" | "students" | "courses" | "enrollments" | "history" | "settings";

const navigation: Array<{ id: Page; label: string }> = [
  { id: "dashboard", label: "ダッシュボード" },
  { id: "attendance", label: "出席受付" },
  { id: "students", label: "学生管理" },
  { id: "courses", label: "授業管理" },
  { id: "enrollments", label: "履修登録" },
  { id: "history", label: "出欠履歴" },
  { id: "settings", label: "RFID設定" },
];

function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof ApiRequestError ? error.message : "処理に失敗しました";
  return <p className="error">{message}</p>;
}

function formatDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("ja-JP") : "—";
}

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [students, setStudents] = useState<Student[]>([]);
  const [courses, setCourses] = useState<Course[]>([]);
  const [openSession, setOpenSession] = useState<Session | null>(null);
  const [runtimeFromSettings, setRuntimeFromSettings] = useState<RuntimeStatus | null>(null);
  const [registrationOperationId, setRegistrationOperationId] = useState<string | null>(null);
  const [attendanceBusy, setAttendanceBusy] = useState(false);
  const [pollingRequested, setPollingRequested] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const reloadBase = useCallback(async () => {
    const [studentRows, courseRows, currentOpen] = await Promise.all([
      api.get<Student[]>("/students"),
      api.get<Course[]>("/courses"),
      api.get<Session | null>("/sessions/open"),
    ]);
    setStudents(studentRows);
    setCourses(courseRows);
    setOpenSession(currentOpen);
  }, []);

  useEffect(() => {
    void reloadBase().catch(setError);
  }, [reloadBase]);

  const handleEvents = useCallback((events: RuntimeEvent[]) => {
    for (const event of events) {
      if (event.kind === "REGISTRATION_COMPLETED" || event.kind === "REGISTRATION_FAILED") {
        setRegistrationOperationId(null);
        void reloadBase();
      }
      if (event.kind === "ATTENDANCE_RESULT") setMessage(String(event.payload.status ?? "受付結果を受信しました"));
    }
  }, [reloadBase]);

  const handleRuntimeStatus = useCallback((status: RuntimeStatus) => {
    setRuntimeFromSettings(status);
    if (status.state === "IDLE" && !registrationOperationId && !attendanceBusy) setPollingRequested(false);
  }, [attendanceBusy, registrationOperationId]);

  const pollingEnabled = Boolean(
    registrationOperationId || (page === "attendance" && (attendanceBusy || pollingRequested || runtimeFromSettings?.state === "STARTING" || runtimeFromSettings?.state === "REGISTERING" || runtimeFromSettings?.state === "ATTENDING" || runtimeFromSettings?.state === "STOPPING" || runtimeFromSettings?.state === "ERROR")),
  );
  const runtime = useRuntimePolling(pollingEnabled, handleEvents, handleRuntimeStatus) ?? runtimeFromSettings;

  const run = async (action: () => Promise<void>) => {
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (caught) {
      setError(caught);
    }
  };

  const refreshRuntime = useCallback(async () => {
    const status = await api.get<RuntimeStatus>("/rfid/runtime");
    setRuntimeFromSettings(status);
    return status;
  }, []);

  const pageContent = useMemo(() => {
    const common = { students, courses, openSession, reloadBase, run, setMessage, setError, setPollingRequested };
    switch (page) {
      case "dashboard": return <DashboardPage {...common} runtime={runtime} onNavigate={setPage} />;
      case "attendance": return <AttendancePage {...common} runtime={runtime} busy={attendanceBusy} setBusy={setAttendanceBusy} />;
      case "students": return <StudentsPage {...common} registrationOperationId={registrationOperationId} setRegistrationOperationId={setRegistrationOperationId} />;
      case "courses": return <CoursesPage {...common} />;
      case "enrollments": return <EnrollmentsPage {...common} />;
      case "history": return <HistoryPage {...common} />;
      case "settings": return <SettingsPage runtime={runtime} refreshRuntime={refreshRuntime} />;
    }
  }, [attendanceBusy, courses, error, openSession, page, registrationOperationId, reloadBase, runtime, run, students, refreshRuntime]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div><span className="eyebrow">LOCAL WEB CONSOLE</span><h1>RFID出欠管理システム</h1></div>
        <span className={`runtime-badge state-${runtime?.state ?? "IDLE"}`}>{runtime?.state ?? "接続確認中"}</span>
      </header>
      <div className="layout">
        <nav className="sidebar" aria-label="メインメニュー">
          {navigation.map((item) => <button className={page === item.id ? "nav-item active" : "nav-item"} key={item.id} onClick={() => setPage(item.id)}>{item.label}</button>)}
        </nav>
        <main className="main-content">
          <div className="notice"><strong>ローカル運用:</strong> CUIとWebの同時利用は行わず、FastAPIは127.0.0.1にのみバインドしてください。</div>
          {message && <p className="success">{message}</p>}
          <ErrorText error={error} />
          {pageContent}
        </main>
      </div>
    </div>
  );
}

type CommonProps = {
  students: Student[];
  courses: Course[];
  openSession: Session | null;
  reloadBase: () => Promise<void>;
  run: (action: () => Promise<void>) => Promise<void>;
  setMessage: (value: string | null) => void;
  setError: (value: unknown) => void;
  setPollingRequested: (value: boolean) => void;
};

function DashboardPage({ students, courses, openSession, runtime, onNavigate }: CommonProps & { runtime: RuntimeStatus | null; onNavigate: (page: Page) => void }) {
  return <section><div className="page-heading"><div><span className="eyebrow">OVERVIEW</span><h2>今日の運用状況</h2></div></div><div className="card-grid"><button className="metric-card" onClick={() => onNavigate("students")}><span>登録学生</span><strong>{students.length}</strong></button><button className="metric-card" onClick={() => onNavigate("courses")}><span>登録授業</span><strong>{courses.length}</strong></button><button className="metric-card" onClick={() => onNavigate("attendance")}><span>受付中の授業回</span><strong>{openSession ? `第${openSession.session_number}回` : "なし"}</strong></button><div className="metric-card"><span>RFID runtime</span><strong>{runtime?.state ?? "IDLE"}</strong></div></div><div className="panel"><h3>利用上の注意</h3><p>Web版は既存CUIと同じattendance.dbを使用します。学生登録・授業登録・履修登録を行う前に、CUIを終了してください。</p></div></section>;
}

function StudentsPage({ students, run, reloadBase, setMessage, registrationOperationId, setRegistrationOperationId }: CommonProps & { registrationOperationId: string | null; setRegistrationOperationId: (value: string | null) => void }) {
  const [name, setName] = useState(""); const [uid, setUid] = useState(""); const [rfidName, setRfidName] = useState(""); const [port, setPort] = useState("COM4");
  return <section><PageHeading eyebrow="STUDENTS" title="学生管理" /><div className="two-column"><form className="panel form-panel" onSubmit={(event) => { event.preventDefault(); void run(async () => { await api.post("/students", { name, card_uid: uid }); setName(""); setUid(""); setMessage("学生を登録しました"); await reloadBase(); }); }}><h3>学生を手動登録</h3><label>氏名<input value={name} onChange={(event) => setName(event.target.value)} required /></label><label>カードUID<input value={uid} onChange={(event) => setUid(event.target.value)} placeholder="8・14・20文字の16進数" required /></label><button className="primary" type="submit">登録する</button></form><form className="panel form-panel" onSubmit={(event) => { event.preventDefault(); void run(async () => { const result = await api.post<{ operation_id: string }>("/rfid/registration/start", { name: rfidName, port, mode: "SERIAL" }); setRegistrationOperationId(result.operation_id); setMessage("カードをかざしてください"); }); }}><h3>RFIDで登録</h3><label>氏名<input value={rfidName} onChange={(event) => setRfidName(event.target.value)} required /></label><label>COMポート<input value={port} onChange={(event) => setPort(event.target.value)} required /></label><button className="primary" type="submit" disabled={Boolean(registrationOperationId)}>読み取り開始</button>{registrationOperationId && <p className="muted">登録処理中です。operation_id: {registrationOperationId}</p>}</form></div><div className="panel"><h3>登録済み学生</h3><DataTable headers={["ID", "氏名", "カードUID"]} rows={students.map((student) => [student.student_id, student.name, student.card_uid])} /></div></section>;
}

function CoursesPage({ courses, run, reloadBase, setMessage }: CommonProps) { const [courseName, setCourseName] = useState(""); return <section><PageHeading eyebrow="COURSES" title="授業管理" /><form className="panel inline-form" onSubmit={(event) => { event.preventDefault(); void run(async () => { await api.post("/courses", { course_name: courseName }); setCourseName(""); setMessage("授業を登録しました"); await reloadBase(); }); }}><label>授業名<input value={courseName} onChange={(event) => setCourseName(event.target.value)} required /></label><button className="primary" type="submit">授業を登録</button></form><div className="panel"><h3>登録済み授業</h3><DataTable headers={["ID", "授業名"]} rows={courses.map((course) => [course.course_id, course.course_name])} /></div></section>; }

function EnrollmentsPage({ students, courses, run, setMessage }: CommonProps) { const [courseId, setCourseId] = useState<number | "">(courses[0]?.course_id ?? ""); const [studentId, setStudentId] = useState<number | "">(students[0]?.student_id ?? ""); const [rows, setRows] = useState<Student[]>([]); const load = async (id: number) => setRows(await api.get<Student[]>(`/courses/${id}/enrollments`)); return <section><PageHeading eyebrow="ENROLLMENTS" title="履修登録" /><div className="panel inline-form"><label>授業<select value={courseId} onChange={(event) => { const value = Number(event.target.value); setCourseId(value); void load(value); }}>{courses.map((course) => <option key={course.course_id} value={course.course_id}>{course.course_name}</option>)}</select></label><label>学生<select value={studentId} onChange={(event) => setStudentId(Number(event.target.value))}>{students.map((student) => <option key={student.student_id} value={student.student_id}>{student.name}</option>)}</select></label><button className="primary" onClick={() => { if (courseId && studentId) void run(async () => { await api.post(`/courses/${courseId}/enrollments`, { student_id: studentId }); setMessage("履修登録しました"); await load(courseId); }); }}>履修登録</button></div><div className="panel"><h3>履修者</h3><DataTable headers={["ID", "氏名", "操作"]} rows={rows.map((student) => [student.student_id, student.name, <button key={student.student_id} onClick={() => { if (courseId) void run(async () => { await api.delete(`/courses/${courseId}/enrollments/${student.student_id}`); setMessage("履修を解除しました"); await load(courseId); }); }}>解除</button>])} /></div></section>; }

function AttendancePage({ courses, openSession, runtime, busy, setBusy, run, setMessage, reloadBase, setPollingRequested }: CommonProps & { runtime: RuntimeStatus | null; busy: boolean; setBusy: (value: boolean) => void }) { const [courseId, setCourseId] = useState<number | "">(courses[0]?.course_id ?? ""); const [mode, setMode] = useState<RuntimeMode>("MOCK"); const [port, setPort] = useState("COM4"); const [uid, setUid] = useState(""); const [rows, setRows] = useState<AttendanceRow[]>([]); const [summary, setSummary] = useState<AttendanceSummary | null>(null); const refresh = async (sessionId: number) => { const [report, totals] = await Promise.all([api.get<AttendanceRow[]>(`/sessions/${sessionId}/attendance`), api.get<AttendanceSummary>(`/sessions/${sessionId}/summary`)]); setRows(report); setSummary(totals); }; return <section><PageHeading eyebrow="ATTENDANCE" title="出席受付" /><div className="panel inline-form"><label>授業<select value={courseId} onChange={(event) => setCourseId(Number(event.target.value))}>{courses.map((course) => <option key={course.course_id} value={course.course_id}>{course.course_name}</option>)}</select></label>{!openSession && <button className="primary" onClick={() => { if (courseId) void run(async () => { await api.post("/sessions", { course_id: courseId }); await reloadBase(); setMessage("授業回を開始しました"); }); }}>授業回を開始</button>}{openSession && <><label>入力方式<select value={mode} onChange={(event) => setMode(event.target.value as RuntimeMode)}><option value="MOCK">MOCK</option><option value="SERIAL">SERIAL</option></select></label>{mode === "SERIAL" && <label>COMポート<input value={port} onChange={(event) => setPort(event.target.value)} /></label>}<button className="primary" disabled={runtime?.state !== "IDLE" || busy} onClick={() => { setPollingRequested(true); void run(async () => { setBusy(true); try { await api.post("/attendance/runtime/start", { session_id: openSession.session_id, mode, port: mode === "SERIAL" ? port : undefined }); setMessage("出席受付を開始しました"); } finally { setBusy(false); } }); }}>受付開始</button>{runtime?.state !== "IDLE" && <button onClick={() => void run(async () => { await api.post("/rfid/runtime/stop"); setBusy(false); setMessage("RFID受付を停止しました"); })}>停止</button>}<button onClick={() => void run(async () => { await api.post(`/sessions/${openSession.session_id}/close`); await reloadBase(); setRows([]); setSummary(null); setMessage("授業回を終了しました"); })} disabled={runtime?.state !== "IDLE"}>受付終了</button></>}</div>{openSession && mode === "MOCK" && runtime?.state === "ATTENDING" && <form className="panel inline-form" onSubmit={(event) => { event.preventDefault(); void run(async () => { await api.post("/attendance/runtime/scan", { card_uid: uid }); setUid(""); if (openSession) await refresh(openSession.session_id); setMessage("カードを受け付けました"); }); }}><label>テスト用UID<input value={uid} onChange={(event) => setUid(event.target.value)} placeholder="MOCK入力" required /></label><button className="primary" type="submit">出席を受付</button></form>}{summary && <div className="card-grid"><div className="metric-card"><span>出席</span><strong>{summary.present_count}</strong></div><div className="metric-card"><span>未受付</span><strong>{summary.pending_count}</strong></div><div className="metric-card"><span>欠席</span><strong>{summary.absent_count}</strong></div></div>}<div className="panel"><h3>現在の出欠</h3><button onClick={() => { if (openSession) void run(() => refresh(openSession.session_id)); }}>再読込</button><DataTable headers={["学生ID", "氏名", "状態", "受付時刻"]} rows={rows.map((row) => [row.student_id, row.name, row.attendance_status, formatDate(row.scanned_at)])} /></div></section>; }

function HistoryPage({ courses, run }: CommonProps) { const [courseId, setCourseId] = useState<number | "">(courses[0]?.course_id ?? ""); const [sessions, setSessions] = useState<Session[]>([]); const [rows, setRows] = useState<AttendanceRow[]>([]); const [summary, setSummary] = useState<AttendanceSummary | null>(null); return <section><PageHeading eyebrow="HISTORY" title="出欠履歴" /><div className="panel inline-form"><label>授業<select value={courseId} onChange={(event) => { const value = Number(event.target.value); setCourseId(value); void run(async () => setSessions(await api.get<Session[]>(`/courses/${value}/sessions`))); }}>{courses.map((course) => <option key={course.course_id} value={course.course_id}>{course.course_name}</option>)}</select></label><select onChange={(event) => { const id = Number(event.target.value); void run(async () => { setRows(await api.get<AttendanceRow[]>(`/sessions/${id}/attendance`)); setSummary(await api.get<AttendanceSummary>(`/sessions/${id}/summary`)); }); }}><option value="">授業回を選択</option>{sessions.map((session) => <option key={session.session_id} value={session.session_id}>第{session.session_number}回 ({session.status})</option>)}</select></div>{summary && <p className="muted">出席 {summary.present_count} / 履修 {summary.enrolled_count}</p>}<div className="panel"><DataTable headers={["学生ID", "氏名", "状態", "受付時刻"]} rows={rows.map((row) => [row.student_id, row.name, row.attendance_status, formatDate(row.scanned_at)])} /></div></section>; }

function SettingsPage({ runtime, refreshRuntime }: { runtime: RuntimeStatus | null; refreshRuntime: () => Promise<RuntimeStatus> }) { const [ports, setPorts] = useState<SerialPort[]>([]); return <section><PageHeading eyebrow="RFID SETTINGS" title="RFID設定" /><div className="panel"><p>Web版は127.0.0.1限定です。COMポートは1本だけ使用し、停止完了後に授業回を終了してください。</p><button onClick={() => void refreshRuntime()}>runtime状態を再読込</button><button onClick={() => void api.get<SerialPort[]>("/rfid/ports").then(setPorts)}>COMポートを再検出</button><pre>{JSON.stringify(runtime, null, 2)}</pre><DataTable headers={["デバイス", "説明", "HWID"]} rows={ports.map((port) => [port.device, port.description, port.hwid])} /></div></section>; }

function PageHeading({ eyebrow, title }: { eyebrow: string; title: string }) { return <div className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div></div>; }

function DataTable({ headers, rows }: { headers: string[]; rows: Array<Array<React.ReactNode>> }) { return <div className="table-wrap"><table><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{rows.length === 0 ? <tr><td colSpan={headers.length} className="muted">データがありません</td></tr> : rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></div>; }
