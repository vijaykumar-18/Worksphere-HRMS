import { useEffect, useState } from "react";

const API = "/api";
const reportTypes = ["employees", "attendance", "leaves", "payroll", "onboarding", "resignations", "tasks", "feedback", "announcements"];

async function request(path, token, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API}${path}`, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "Request failed" }));
    const detail = body.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((item) => item.msg).join("; ") : "Request failed");
  }
  if (response.status === 204) return undefined;
  return response.json();
}

function date(value) {
  return value ? new Date(`${String(value).slice(0, 10)}T00:00:00`).toLocaleDateString() : "—";
}

function field(value, key, update) {
  return (event) => update({ ...value, [key]: event.target.value });
}

function parseCsv(text) {
  const records = [];
  let record = [];
  let value = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted && character === "\"" && text[index + 1] === "\"") {
      value += "\"";
      index += 1;
    } else if (character === "\"") {
      quoted = !quoted;
    } else if (character === "," && !quoted) {
      record.push(value);
      value = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      if (character === "\r" && text[index + 1] === "\n") index += 1;
      record.push(value);
      if (record.some((item) => item.length)) records.push(record);
      record = [];
      value = "";
    } else {
      value += character;
    }
  }
  record.push(value);
  if (record.some((item) => item.length)) records.push(record);
  return records;
}

export default function NewModules({ page, user, token, onError, onMessage }) {
  const [rows, setRows] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [profile, setProfile] = useState(null);
  const [preferences, setPreferences] = useState({
    email_notifications: false,
    task_reminders: true,
    announcement_notifications: true,
  });
  const [integrationStatus, setIntegrationStatus] = useState({
    email_configured: false,
    google_calendar_configured: false,
    google_calendar_connected: false,
  });
  const [selectedAnnouncement, setSelectedAnnouncement] = useState(null);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({});
  const isManager = user.role !== "EMPLOYEE";

  async function load() {
    setLoading(true);
    onError("");
    try {
      if (page === "Profile & settings") {
        const [employee, settings] = await Promise.all([
          request("/employees/me", token),
          request("/settings/me", token),
        ]);
        setProfile(employee);
        setPreferences(settings);
        return;
      }
      if (page === "Integrations & exports") {
        setIntegrationStatus(await request("/integrations/status", token));
      }

      const endpoints = {
        Onboarding: "/onboarding",
        Resignations: "/resignations",
        Announcements: "/announcements",
        Tasks: "/tasks",
        Feedback: "/feedback",
      };
      if (endpoints[page]) {
        const loaded = await request(endpoints[page], token);
        setRows(loaded);
      }
      if (isManager && ["Onboarding", "Tasks"].includes(page)) {
        setEmployees(await request("/employees", token));
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : "Could not load module data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    setForm({});
    setSelectedAnnouncement(null);
  }, [page, token]);

  useEffect(() => {
    function handleCalendarMessage(event) {
      if (event.origin !== integrationStatus.google_calendar_callback_origin || event.data?.type !== "worksphere-google-calendar") return;
      if (event.data.status === "connected") {
        onMessage("Google Calendar connected.");
        void load();
      } else {
        onError(event.data.message || "Google Calendar authorization was not completed.");
      }
    }
    window.addEventListener("message", handleCalendarMessage);
    return () => window.removeEventListener("message", handleCalendarMessage);
  }, [page, token, integrationStatus.google_calendar_callback_origin]);

  async function submit(path, method, body, success) {
    onError("");
    try {
      await request(path, token, {
        method,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      onMessage(success);
      await load();
    } catch (error) {
      onError(error instanceof Error ? error.message : "Could not save changes.");
    }
  }

  async function exportCsv() {
    onError("");
    try {
      const response = await fetch(`${API}/exports/reports.csv?report_type=${encodeURIComponent(form.report_type || "employees")}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: "Export failed" }));
        throw new Error(body.detail || "Export failed");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${form.report_type || "employees"}-report.csv`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Could not export report.");
    }
  }

  async function exportPdf() {
    const printWindow = window.open("", "_blank");
    if (!printWindow) {
      onError("Allow pop-ups to generate the PDF report.");
      return;
    }
    onError("");
    try {
      const reportType = form.report_type || "employees";
      const response = await fetch(`${API}/exports/reports.csv?report_type=${encodeURIComponent(reportType)}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: "Export failed" }));
        throw new Error(body.detail || "Export failed");
      }
      const [headers = [], ...records] = parseCsv(await response.text());
      const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
      })[character]);
      const table = `<table><thead><tr>${headers.map((value) => `<th>${escapeHtml(value)}</th>`).join("")}</tr></thead><tbody>${records.map((record) => `<tr>${headers.map((_, index) => `<td>${escapeHtml(record[index] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      printWindow.document.write(`<!doctype html><html><head><title>${escapeHtml(reportType)} report</title><style>body{font:12px Arial,sans-serif;color:#202a3a;padding:24px}h1{font-size:20px}p{color:#667085}table{width:100%;border-collapse:collapse;font-size:10px}th,td{border:1px solid #dfe3eb;padding:7px;text-align:left;overflow-wrap:anywhere}th{background:#f1f3f8}@media print{body{padding:0}}</style></head><body><h1>${escapeHtml(reportType[0].toUpperCase() + reportType.slice(1))} report</h1><p>Generated ${escapeHtml(new Date().toLocaleString())}</p>${table}</body></html>`);
      printWindow.document.close();
      window.setTimeout(() => {
        printWindow.focus();
        printWindow.print();
      }, 300);
    } catch (error) {
      printWindow.close();
      onError(error instanceof Error ? error.message : "Could not generate PDF report.");
    }
  }

  async function exportCalendar() {
    onError("");
    try {
      const [onboarding, tasks] = await Promise.all([
        request("/onboarding", token),
        request("/tasks", token),
      ]);
      const events = [
        ...onboarding.map((item) => ({ title: item.task_name, due: item.due_date, status: item.status })),
        ...tasks.map((item) => ({ title: item.title, due: item.due_date, status: item.status })),
      ].filter((item) => item.due && item.status !== "COMPLETED");
      const escape = (text) => String(text).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
      const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//WorkSphere HRMS//Calendar//EN"];
      for (const [index, item] of events.entries()) {
        const day = String(item.due).replaceAll("-", "");
        lines.push("BEGIN:VEVENT", `UID:worksphere-${index}-${day}@hrms`, `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "")}`, `DTSTART;VALUE=DATE:${day}`, `SUMMARY:${escape(item.title)}`, "END:VEVENT");
      }
      lines.push("END:VCALENDAR");
      const blob = new Blob([lines.join("\r\n")], { type: "text/calendar;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "worksphere-tasks.ics";
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Could not export calendar.");
    }
  }

  async function connectGoogleCalendar() {
    const popup = window.open("about:blank", "worksphere-google-calendar", "popup,width=600,height=700");
    if (!popup) {
      onError("Allow pop-ups to connect Google Calendar.");
      return;
    }
    try {
      const result = await request("/integrations/google/authorize", token);
      popup.location.href = result.authorization_url;
    } catch (error) {
      popup.close();
      onError(error instanceof Error ? error.message : "Could not start Google Calendar authorization.");
    }
  }

  async function syncGoogleCalendar() {
    try {
      const result = await request("/integrations/google/sync", token, { method: "POST" });
      onMessage(`Calendar sync complete: ${result.created} created, ${result.updated} updated, ${result.removed} removed.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Could not sync Google Calendar.");
    }
  }

  async function disconnectGoogleCalendar() {
    await submit("/integrations/google", "DELETE", undefined, "Google Calendar disconnected.");
    try {
      setIntegrationStatus(await request("/integrations/status", token));
    } catch (error) {
      onError(error instanceof Error ? error.message : "Could not refresh integration status.");
    }
  }

  async function sendTestEmail() {
    try {
      const result = await request("/integrations/email/test", token, { method: "POST" });
      onMessage(result.message);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Could not send test email.");
    }
  }

  if (loading) return <div className="ws-module-loading">Loading {page.toLowerCase()}…</div>;

  if (page === "Onboarding") {
    const completed = rows.filter((item) => item.status === "COMPLETED").length;
    const percent = rows.length ? Math.round((completed / rows.length) * 100) : 0;
    const employeeProgress = [...new Set(rows.map((item) => String(item.employee_id)))].map((employeeId) => {
      const tasks = rows.filter((item) => String(item.employee_id) === employeeId);
      const completedTasks = tasks.filter((item) => item.status === "COMPLETED").length;
      return {
        employeeId,
        employeeName: tasks[0].employee_name || `Employee #${employeeId}`,
        completedTasks,
        totalTasks: tasks.length,
      };
    });
    return <div className="ws-page-stack">
      <section className="ws-panel">
        <div className="ws-module-heading"><div><h2>Onboarding checklist</h2><p>Track assigned tasks and new hire progress.</p></div><strong>{percent}% complete</strong></div>
        <div className="ws-progress"><span style={{ width: `${percent}%` }} /></div>
        <p className="ws-module-subtitle">{completed} of {rows.length} tasks completed</p>
        {isManager && employeeProgress.map((employee) => <div className="ws-employee-progress" key={employee.employeeId}>
          <div><b>{employee.employeeName}</b><small>{employee.completedTasks} of {employee.totalTasks} tasks completed</small></div>
          <div className="ws-progress"><span style={{ width: `${Math.round((employee.completedTasks / employee.totalTasks) * 100)}%` }} /></div>
        </div>)}
      </section>
      {isManager && <form className="ws-panel ws-module-form" onSubmit={(event) => {
        event.preventDefault();
        void submit("/onboarding", "POST", { ...form, employee_id: Number(form.employee_id) }, "Onboarding task assigned.");
      }}>
        <h3>Assign onboarding task</h3>
        <label>Employee<select required value={form.employee_id || ""} onChange={field(form, "employee_id", setForm)}><option value="">Select employee</option>{employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name}</option>)}</select></label>
        <label>Task<input required maxLength={200} value={form.task_name || ""} onChange={field(form, "task_name", setForm)} /></label>
        <label>Due date<input required type="date" value={form.due_date || ""} onChange={field(form, "due_date", setForm)} /></label>
        <button className="ws-primary">Assign task</button>
      </form>}
      <div className="ws-module-list">{rows.length ? rows.map((item) => <article className="ws-module-item" key={item.id}>
        <div><b>{item.task_name}</b><small>{isManager ? `${item.employee_name || `Employee #${item.employee_id}`} · ` : ""}Due {date(item.due_date)}</small></div>
        <span className={`ws-module-status ${item.status.toLowerCase()}`}>{item.status}</span>
        {item.status !== "COMPLETED" && <button className="ws-secondary" onClick={() => void submit(`/onboarding/${item.id}`, "PATCH", { status: "COMPLETED" }, "Checklist task marked complete.")}>Complete</button>}
      </article>) : <p className="ws-module-empty">No onboarding tasks have been assigned.</p>}</div>
    </div>;
  }

  if (page === "Resignations") return <div className="ws-page-stack">
    <section className="ws-panel"><h2>Exit status</h2><p>Submit a resignation request or review its current approval status.</p></section>
    {!isManager && <form className="ws-panel ws-module-form" onSubmit={(event) => {
      event.preventDefault();
      void submit("/resignations", "POST", { ...form, notice_period: Number(form.notice_period) }, "Resignation request submitted.");
    }}>
      <h3>Submit resignation</h3>
      <label>Reason<textarea required maxLength={5000} rows="4" value={form.reason || ""} onChange={field(form, "reason", setForm)} /></label>
      <label>Notice period (days)<input required type="number" min="0" max="365" value={form.notice_period ?? 30} onChange={field(form, "notice_period", setForm)} /></label>
      <button className="ws-primary">Submit request</button>
    </form>}
    <div className="ws-module-list">{rows.length ? rows.map((item) => <article className="ws-module-item" key={item.id}>
      <div><b>{isManager ? item.employee_name : "My resignation"} · {item.notice_period} day notice</b><small>{item.reason} · Submitted {new Date(item.created_at).toLocaleDateString()}</small></div>
      <span className={`ws-module-status ${item.status.toLowerCase()}`}>{item.status}</span>
      {isManager && item.status === "PENDING" && <div className="ws-module-actions"><button className="ws-mini-button good" onClick={() => void submit(`/resignations/${item.id}/decision`, "PATCH", { status: "APPROVED" }, "Resignation approved.")}>Approve</button><button className="ws-mini-button danger" onClick={() => void submit(`/resignations/${item.id}/decision`, "PATCH", { status: "REJECTED" }, "Resignation rejected.")}>Reject</button></div>}
    </article>) : <p className="ws-module-empty">No resignation requests found.</p>}</div>
  </div>;

  if (page === "Announcements") return <div className="ws-page-stack">
    <section className="ws-panel"><h2>Announcement board</h2><p>Updates and notices shared with the organization.</p></section>
    {isManager && <form className="ws-panel ws-module-form" onSubmit={(event) => {
      event.preventDefault();
      void submit("/announcements", "POST", form, "Announcement posted.");
    }}>
      <h3>Post an announcement</h3>
      <label>Title<input required maxLength={200} value={form.title || ""} onChange={field(form, "title", setForm)} /></label>
      <label>Message<textarea required maxLength={10000} rows="4" value={form.message || ""} onChange={field(form, "message", setForm)} /></label>
      <button className="ws-primary">Publish</button>
    </form>}
    <div className="ws-module-list">{rows.length ? rows.map((item) => <article className="ws-module-item ws-announcement" key={item.id}>
      <button className="ws-announcement-title" onClick={() => {
        setSelectedAnnouncement(selectedAnnouncement?.id === item.id ? null : item);
      }}>{item.title}</button><small>By {item.created_by_name} · {new Date(item.created_at).toLocaleString()}</small>
      {selectedAnnouncement?.id === item.id && <p className="ws-announcement-detail">{item.message}</p>}
    </article>) : <p className="ws-module-empty">No announcements have been posted.</p>}</div>
  </div>;

  if (page === "Tasks") return <div className="ws-page-stack">
    <section className="ws-panel"><h2>Task dashboard</h2><p>View assignments, due dates and completion status.</p></section>
    {isManager && <form className="ws-panel ws-module-form" onSubmit={(event) => {
      event.preventDefault();
      void submit("/tasks", "POST", { ...form, assigned_to: Number(form.assigned_to) }, "Task assigned.");
    }}>
      <h3>Assign an internal task</h3>
      <label>Employee<select required value={form.assigned_to || ""} onChange={field(form, "assigned_to", setForm)}><option value="">Select employee</option>{employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name}</option>)}</select></label>
      <label>Title<input required maxLength={200} value={form.title || ""} onChange={field(form, "title", setForm)} /></label>
      <label>Description<textarea maxLength={5000} rows="3" value={form.description || ""} onChange={field(form, "description", setForm)} /></label>
      <label>Due date<input required type="date" value={form.due_date || ""} onChange={field(form, "due_date", setForm)} /></label>
      <button className="ws-primary">Assign task</button>
    </form>}
    <div className="ws-module-list">{rows.length ? rows.map((item) => <article className="ws-module-item" key={item.id}>
      <div><b>{item.title}</b><small>{isManager ? `${item.employee_name} · ` : ""}{item.description} · Due {date(item.due_date)}</small></div>
      <span className={`ws-module-status ${item.status.toLowerCase()}`}>{item.status}</span>
      {item.status !== "COMPLETED" && <button className="ws-secondary" onClick={() => void submit(`/tasks/${item.id}`, "PATCH", { status: "COMPLETED" }, "Task marked complete.")}>Complete</button>}
    </article>) : <p className="ws-module-empty">No tasks assigned.</p>}</div>
  </div>;

  if (page === "Feedback") return <div className="ws-page-stack">
    <section className="ws-panel"><h2>{isManager ? "Feedback dashboard" : "Employee feedback"}</h2><p>{isManager ? "Feedback submitted by employees." : "Share feedback with the HR team."}</p></section>
    {!isManager && <form className="ws-panel ws-module-form" onSubmit={(event) => {
      event.preventDefault();
      void submit("/feedback", "POST", { ...form, rating: Number(form.rating) }, "Feedback submitted.");
    }}>
      <h3>Submit feedback</h3>
      <label>Rating<select value={form.rating ?? 5} onChange={field(form, "rating", setForm)}>{[5, 4, 3, 2, 1].map((rating) => <option key={rating} value={rating}>{rating} / 5</option>)}</select></label>
      <label>Feedback<textarea required maxLength={5000} rows="4" value={form.message || ""} onChange={field(form, "message", setForm)} /></label>
      <button className="ws-primary">Send feedback</button>
    </form>}
    <div className="ws-module-list">{rows.length ? rows.map((item) => <article className="ws-module-item" key={item.id}>
      <div><b>{isManager ? item.employee_name : `${item.rating} / 5 rating`}</b><small>{item.message} · {new Date(item.created_at).toLocaleString()}</small></div>
      {isManager && <span className="ws-module-status">{item.rating} / 5</span>}
    </article>) : <p className="ws-module-empty">No feedback submitted yet.</p>}</div>
  </div>;

  if (page === "Profile & settings") return <div className="ws-page-stack">
    {profile && <form className="ws-panel ws-module-form" onSubmit={(event) => {
      event.preventDefault();
      void submit("/employees/me", "PATCH", { full_name: form.full_name ?? profile.full_name, email: form.email ?? profile.email }, "Profile updated.");
    }}>
      <h2>Profile settings</h2>
      <label>Full name<input required maxLength={160} value={form.full_name ?? profile.full_name} onChange={field(form, "full_name", setForm)} /></label>
      <label>Email<input required type="email" value={form.email ?? profile.email} onChange={field(form, "email", setForm)} /></label>
      <button className="ws-primary">Save profile</button>
    </form>}
    <form className="ws-panel ws-module-form" onSubmit={(event) => {
      event.preventDefault();
      void submit("/auth/change-password", "POST", { current_password: form.current_password, new_password: form.new_password }, "Password changed.");
    }}>
      <h3>Change password</h3>
      <label>Current password<input required type="password" autoComplete="current-password" value={form.current_password || ""} onChange={field(form, "current_password", setForm)} /></label>
      <label>New password (at least 10 characters)<input required minLength={10} maxLength={128} type="password" autoComplete="new-password" value={form.new_password || ""} onChange={field(form, "new_password", setForm)} /></label>
      <button className="ws-primary">Change password</button>
    </form>
    <form className="ws-panel ws-preferences" onSubmit={(event) => {
      event.preventDefault();
      void submit("/settings/me", "PUT", preferences, "Notification preferences saved.");
    }}>
      <h3>Notification preferences</h3>
      <p>Email delivery requires SMTP settings in the backend environment.</p>
      {[
        ["email_notifications", "Email notifications"],
        ["task_reminders", "Task and onboarding assignment emails"],
        ["announcement_notifications", "Announcement notifications"],
      ].map(([key, label]) => <label key={key}><input type="checkbox" checked={Boolean(preferences[key])} onChange={(event) => setPreferences({ ...preferences, [key]: event.target.checked })} />{label}</label>)}
      <button className="ws-primary">Save preferences</button>
    </form>
  </div>;

  if (page === "Integrations & exports") return <div className="ws-page-stack">
    <section className="ws-panel"><h2>Reports and integrations</h2><p>Export authorized reports, print to PDF, or download task dates for your calendar.</p></section>
    <section className="ws-panel ws-integration-card">
      <div><h3>Google Calendar</h3><p>{integrationStatus.google_calendar_connected ? "Calendar connected to your account." : integrationStatus.google_calendar_configured ? "Connect your Google account to sync assigned due dates." : "Google OAuth credentials must be configured by the administrator in backend .env."}</p></div>
      <div className="ws-module-actions">
        {!integrationStatus.google_calendar_connected && <button className="ws-primary" disabled={!integrationStatus.google_calendar_configured} onClick={() => void connectGoogleCalendar()}>Connect Google Calendar</button>}
        {integrationStatus.google_calendar_connected && <><button className="ws-primary" onClick={() => void syncGoogleCalendar()}>Sync my tasks</button><button className="ws-secondary" onClick={() => void disconnectGoogleCalendar()}>Disconnect</button></>}
      </div>
    </section>
    <section className="ws-panel ws-integration-card">
      <div><h3>Email notifications</h3><p>{integrationStatus.email_configured ? `SMTP is configured. A test email can be sent to ${user.email}.` : "SMTP credentials must be configured by the administrator in backend .env."}</p></div>
      <button className="ws-secondary" disabled={!integrationStatus.email_configured} onClick={() => void sendTestEmail()}>Send test email</button>
    </section>
    {isManager && <section className="ws-panel ws-module-form">
      <h3>Export report</h3>
      <label>Report<select value={form.report_type || "employees"} onChange={field(form, "report_type", setForm)}>{reportTypes.map((type) => <option key={type} value={type}>{type[0].toUpperCase() + type.slice(1)}</option>)}</select></label>
      <div className="ws-module-actions"><button className="ws-primary" onClick={() => void exportCsv()}>Download CSV</button><button className="ws-secondary" onClick={() => void exportPdf()}>Print / Save report as PDF</button></div>
    </section>}
    <section className="ws-panel ws-module-form">
      <h3>Calendar sync</h3>
      <p>Download pending onboarding and assigned tasks as an iCalendar (.ics) file, then import it into Google Calendar or another calendar app.</p>
      <button className="ws-secondary" onClick={() => void exportCalendar()}>Download calendar file</button>
    </section>
  </div>;

  return null;
}
