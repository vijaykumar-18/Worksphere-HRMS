import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Bell, BriefcaseBusiness, CalendarDays, ChartNoAxesCombined, Check, ChevronRight, Clock3, FileText, LayoutDashboard, LogOut, Search, ShieldCheck, Star, Users, UserCog, Wallet, X, CalendarRange, FileUp, } from "lucide-react";
import NewModules from "./NewModules";
const API = "/api";
const money = (n) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(Number(n ?? 0));
const dateLabel = (v) => v ? new Date(String(v)).toLocaleDateString() : "—";
async function request(path, token, options = {}) {
    const headers = new Headers(options.headers ?? {});
    if (token)
        headers.set("Authorization", `Bearer ${token}`);
    if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type"))
        headers.set("Content-Type", "application/json");
    const response = await fetch(`${API}${path}`, { ...options, headers });
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: "Request failed" }));
        const detail = error.detail;
        throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((x) => x.msg).join("; ") : "Request failed");
    }
    if (response.status === 204)
        return undefined;
    return response.json();
}
function App() {
    const [token, setToken] = useState(() => localStorage.getItem("worksphere_token"));
    const [user, setUser] = useState(null);
    const [page, setPage] = useState("Overview");
    const [data, setData] = useState([]);
    const [summary, setSummary] = useState({});
    const [report, setReport] = useState({});
    const [employees, setEmployees] = useState([]);
    const [shifts, setShifts] = useState([]);
    const [notifications, setNotifications] = useState([]);
    const [search, setSearch] = useState("");
    const [department, setDepartment] = useState("");
    const [statusFilter, setStatusFilter] = useState("");
    const [roleFilter, setRoleFilter] = useState("");
    const [attendanceFilter, setAttendanceFilter] = useState("");
    const [minRating, setMinRating] = useState("");
    const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
    const [startDate, setStartDate] = useState(new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString().slice(0, 10));
    const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10));
    const [message, setMessage] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const nav = [
        { title: "Overview", icon: <LayoutDashboard size={18}/> },
        { title: "My profile", icon: <UserCog size={18}/> },
        { title: "Employees", icon: <Users size={18}/>, roles: ["HR", "ADMIN"] },
        { title: "Attendance", icon: <Clock3 size={18}/> },
        { title: "Leave management", icon: <CalendarDays size={18}/> },
        { title: "Payroll", icon: <Wallet size={18}/> },
        { title: "Notifications", icon: <Bell size={18}/> },
        { title: "User management", icon: <UserCog size={18}/>, roles: ["ADMIN"] },
        { title: "Leave policies", icon: <BriefcaseBusiness size={18}/>, roles: ["HR", "ADMIN"] },
        { title: "Shifts", icon: <CalendarRange size={18}/>, roles: ["HR", "ADMIN"] },
        { title: "Leave calendar", icon: <CalendarDays size={18}/> },
        { title: "Documents", icon: <FileUp size={18}/> },
        { title: "Performance", icon: <Star size={18}/> },
        { title: "Analytics", icon: <ChartNoAxesCombined size={18}/>, roles: ["HR", "ADMIN"] },
        { title: "Advanced search", icon: <Search size={18}/>, roles: ["HR", "ADMIN"] },
        { title: "Onboarding", icon: <Check size={18}/> },
        { title: "Resignations", icon: <CalendarRange size={18}/> },
        { title: "Announcements", icon: <Bell size={18}/> },
        { title: "Tasks", icon: <BriefcaseBusiness size={18}/> },
        { title: "Feedback", icon: <Star size={18}/> },
        { title: "Profile & settings", icon: <UserCog size={18}/> },
        { title: "Integrations & exports", icon: <FileText size={18}/> },
    ];
    const loadPage = useCallback(async (activeToken, activePage, currentUser) => {
        setLoading(true);
        setError("");
        try {
            const get = (path) => request(path, activeToken);
            const [mySummary, profile, myNotifications] = await Promise.all([
                get("/dashboard/summary"), get("/employees/me"), get("/notifications"),
            ]);
            setSummary(mySummary);
            setNotifications(myNotifications);
            const managedEmployees = currentUser.role !== "EMPLOYEE" ? await get(`/employees?q=${encodeURIComponent(search)}${department ? `&department=${encodeURIComponent(department)}` : ""}${statusFilter ? `&status=${statusFilter}` : ""}${roleFilter ? `&role=${roleFilter}` : ""}`) : [];
            setEmployees(managedEmployees);
            let rows = [];
            if (activePage === "Overview")
                rows = [];
            if (activePage === "My profile")
                rows = [profile];
            if (activePage === "Employees")
                rows = managedEmployees;
            if (activePage === "Attendance")
                rows = await get(`/attendance${startDate ? `?start_date=${startDate}` : ""}${endDate ? `${startDate ? "&" : "?"}end_date=${endDate}` : ""}`);
            if (activePage === "Leave management") {
                const [leaves, balances] = await Promise.all([get("/leaves"), get("/leave-balances")]);
                rows = leaves.map((x) => ({ ...x, record_type: "LEAVE" }));
                setData(rows);
                // Balances are displayed separately in the page from the second API call.
                sessionStorage.setItem("worksphere_leave_balances", JSON.stringify(balances));
            }
            if (activePage === "Payroll")
                rows = await get(`/payroll?month=${month}`);
            if (activePage === "Notifications")
                rows = myNotifications;
            if (activePage === "User management")
                rows = await get(`/admin/users?q=${encodeURIComponent(search)}`);
            if (activePage === "Leave policies") {
                const [policies, balances] = await Promise.all([get("/leave-policies"), get("/leave-balances")]);
                rows = policies.map((x) => ({ ...x, record_type: "POLICY" }));
                sessionStorage.setItem("worksphere_leave_balances", JSON.stringify(balances));
            }
            if (activePage === "Shifts") {
                const [shiftRows, assignments] = await Promise.all([get("/shifts"), get("/shift-assignments")]);
                setShifts(shiftRows);
                rows = assignments.map((x) => ({ ...x, record_type: "ASSIGNMENT" }));
            }
            if (activePage === "Leave calendar")
                rows = await get(`/leave-calendar?start_date=${startDate}&end_date=${endDate}${department ? `&department=${encodeURIComponent(department)}` : ""}`);
            if (activePage === "Documents")
                rows = await get("/documents");
            if (activePage === "Performance")
                rows = await get("/performance");
            if (activePage === "Analytics")
                setReport(await get(`/analytics/reports?start_date=${startDate}&end_date=${endDate}${department ? `&department=${encodeURIComponent(department)}` : ""}`));
            if (activePage === "Advanced search")
                rows = await get(`/employees/search/advanced?q=${encodeURIComponent(search)}${department ? `&department=${encodeURIComponent(department)}` : ""}${statusFilter ? `&status=${statusFilter}` : ""}${roleFilter ? `&role=${roleFilter}` : ""}${attendanceFilter ? `&attendance_status=${attendanceFilter}` : ""}${minRating ? `&min_rating=${minRating}` : ""}`);
            if (activePage === "Overview")
                setData([]);
            else
                setData(rows);
            void profile;
        }
        catch (err) {
            const text = err instanceof Error ? err.message : "Could not load data";
            setError(text);
            if (text.toLowerCase().includes("token") || text.toLowerCase().includes("credentials"))
                signOut();
        }
        finally {
            setLoading(false);
        }
    }, [search, department, statusFilter, roleFilter, attendanceFilter, minRating, startDate, endDate, month]);
    useEffect(() => {
        if (!token) {
            setUser(null);
            return;
        }
        let alive = true;
        request("/auth/me", token).then((current) => { if (alive)
            setUser(current); }).catch(() => { if (alive)
            signOut(); });
        return () => { alive = false; };
    }, [token]);
    useEffect(() => { if (token && user)
        void loadPage(token, page, user); }, [token, user, page, loadPage]);
    useEffect(() => {
        if (!token)
            return;
        const refreshNotifications = async () => {
            try {
                const latest = await request("/notifications", token);
                setNotifications(latest);
                if (page === "Notifications")
                    setData(latest);
            }
            catch { /* The normal request flow handles expired sessions on navigation or action. */ }
        };
        const timer = window.setInterval(() => { void refreshNotifications(); }, 30000);
        return () => window.clearInterval(timer);
    }, [token, page]);
    function signOut() { localStorage.removeItem("worksphere_token"); setToken(null); setUser(null); setData([]); setEmployees([]); setNotifications([]); }
    async function login(username, password, register) {
        setError("");
        try {
            if (register)
                await request("/auth/register", null, { method: "POST", body: JSON.stringify({ username, password, email: register.email, full_name: register.full_name }) });
            const result = await request("/auth/login", null, { method: "POST", body: JSON.stringify({ username, password }) });
            localStorage.setItem("worksphere_token", result.access_token);
            setToken(result.access_token);
            setMessage(register ? "Account created successfully." : "Welcome back.");
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Sign in failed");
        }
    }
    async function action(path, method = "POST", body, success = "Changes saved.") {
        if (!token)
            return;
        setError("");
        setMessage("");
        try {
            await request(path, token, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
            setMessage(success);
            if (user)
                await loadPage(token, page, user);
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Action failed");
        }
    }
    async function uploadDocument(formData) {
        if (!token)
            return;
        try {
            if (user?.role === "EMPLOYEE") {
                const profile = await request("/employees/me", token);
                formData.set("employee_id", String(profile.id));
            }
            await request("/documents", token, { method: "POST", body: formData });
            setMessage("Document uploaded securely.");
            if (user)
                await loadPage(token, page, user);
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Upload failed");
        }
    }
    async function downloadPayslip(row) {
        if (!token)
            return;
        try {
            const response = await fetch(`${API}/payroll/${row.id}/payslip.csv`, { headers: { Authorization: `Bearer ${token}` } });
            if (!response.ok)
                throw new Error("Payslip download failed");
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = `payslip-${String(row.employee_id)}-${String(row.month)}.csv`;
            link.click();
            URL.revokeObjectURL(url);
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Payslip download failed");
        }
    }
    async function downloadDocument(row) {
        if (!token)
            return;
        try {
            const response = await fetch(`${API}/documents/${row.id}/download`, { headers: { Authorization: `Bearer ${token}` } });
            if (!response.ok)
                throw new Error("Download failed");
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = String(row.file_name ?? "document");
            link.click();
            URL.revokeObjectURL(url);
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Download failed");
        }
    }
    if (!token || !user)
        return <LoginScreen onLogin={login} error={error}/>;
    const visibleNav = nav.filter((item) => !item.roles || item.roles.includes(user.role));
    const unread = notifications.filter((x) => x.is_read !== true).length;
    return <div className="ws-shell">
    <aside className="ws-sidebar">
      <div className="ws-brand"><span className="ws-brand-mark"><Activity size={21}/></span><span>Work<span>Sphere</span><small>PEOPLE OPERATIONS</small></span></div>
      <div className="ws-side-label">WORKSPACE</div>
      <nav className="ws-nav">{visibleNav.map((item) => <button key={item.title} className={page === item.title ? "active" : ""} onClick={() => setPage(item.title)}>{item.icon}<span>{item.title}</span>{item.title === "Notifications" && unread > 0 && <i>{unread}</i>}</button>)}</nav>
      <div className="ws-sidebar-footer"><div className="ws-secure"><ShieldCheck size={17}/><span><b>Secure workspace</b><small>Role-based access enabled</small></span></div><button className="ws-account" onClick={signOut}><span className="ws-avatar">{user.username.slice(0, 1).toUpperCase()}</span><span><b>{user.username}</b><small>{user.role}</small></span><LogOut size={17}/></button></div>
    </aside>
    <main className="ws-main">
      <header className="ws-topbar"><div><span className="ws-eyebrow">WORKSPACE / {page.toUpperCase()}</span><h1>{page}</h1></div><div className="ws-top-right"><span className={`ws-role ${user.role.toLowerCase()}`}>{user.role}</span><span className="ws-today">{new Date().toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}</span><button className="ws-icon-button" title="Notifications" onClick={() => setPage("Notifications")}><Bell size={18}/>{unread > 0 && <i />}</button></div></header>
      {error && <div className="ws-alert error"><X size={17}/>{error}<button onClick={() => setError("")}><X size={15}/></button></div>}
      {message && <div className="ws-alert success"><Check size={17}/>{message}<button onClick={() => setMessage("")}><X size={15}/></button></div>}
      {loading && <div className="ws-loading">Refreshing workspace…</div>}
      {page === "Overview" && <Overview summary={summary} user={user} onNavigate={setPage}/>}
      {page === "My profile" && <ProfilePage rows={data}/>}
      {page === "Employees" && <EmployeesPage rows={data} search={search} setSearch={setSearch} department={department} setDepartment={setDepartment} statusFilter={statusFilter} setStatusFilter={setStatusFilter} roleFilter={roleFilter} setRoleFilter={setRoleFilter} action={action}/>}
      {page === "Attendance" && <AttendancePage rows={data} user={user} action={action} startDate={startDate} setStartDate={setStartDate} endDate={endDate} setEndDate={setEndDate}/>}
      {page === "Leave management" && <LeavePage rows={data} user={user} action={action}/>}
      {page === "Payroll" && <PayrollPage rows={data} user={user} employees={employees} month={month} setMonth={setMonth} action={action} onDownload={downloadPayslip}/>}
      {page === "Notifications" && <NotificationsPage rows={data} action={action}/>}
      {page === "User management" && <UsersPage rows={data} search={search} setSearch={setSearch} action={action}/>}
      {page === "Leave policies" && <PoliciesPage rows={data} employees={employees} action={action}/>}
      {page === "Shifts" && <ShiftsPage rows={data} shifts={shifts} employees={employees} action={action}/>}
      {page === "Leave calendar" && <CalendarPage rows={data} startDate={startDate} setStartDate={setStartDate} endDate={endDate} setEndDate={setEndDate} department={department} setDepartment={setDepartment}/>}
      {page === "Documents" && <DocumentsPage rows={data} user={user} employees={employees} onUpload={uploadDocument} onDownload={downloadDocument} action={action}/>}
      {page === "Performance" && <PerformancePage rows={data} user={user} employees={employees} action={action}/>}
      {page === "Analytics" && <AnalyticsPage report={report} startDate={startDate} setStartDate={setStartDate} endDate={endDate} setEndDate={setEndDate} department={department} setDepartment={setDepartment}/>}
      {page === "Advanced search" && <AdvancedSearchPage rows={data} search={search} setSearch={setSearch} department={department} setDepartment={setDepartment} statusFilter={statusFilter} setStatusFilter={setStatusFilter} roleFilter={roleFilter} setRoleFilter={setRoleFilter} attendanceFilter={attendanceFilter} setAttendanceFilter={setAttendanceFilter} minRating={minRating} setMinRating={setMinRating}/>}
      {["Onboarding", "Resignations", "Announcements", "Tasks", "Feedback", "Profile & settings", "Integrations & exports"].includes(page) && <NewModules page={page} user={user} token={token} onError={setError} onMessage={setMessage}/>}
      <footer className="ws-footer">WorkSphere HRMS <span>•</span> Phase 1–5 workspace <span>•</span> <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">API documentation <ChevronRight size={13}/></a></footer>
    </main>
  </div>;
}
function LoginScreen({ onLogin, error }) {
    const [register, setRegister] = useState(false);
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [email, setEmail] = useState("");
    const [fullName, setFullName] = useState("");
    function submit(e) { e.preventDefault(); void onLogin(username, password, register ? { email, full_name: fullName } : undefined); }
    return <div className="ws-login"><div className="ws-login-art"><div className="ws-brand login-brand"><span className="ws-brand-mark"><Activity size={22}/></span><span>Work<span>Sphere</span><small>PEOPLE OPERATIONS</small></span></div><div className="ws-login-copy"><span className="ws-eyebrow">HUMAN RESOURCES PLATFORM</span><h1>People first.<br /><em>Work better.</em></h1><p>A connected workspace for employee experience, attendance, leave, payroll and performance.</p><div className="ws-login-points"><span><Check size={15}/> Secure role-based access</span><span><Check size={15}/> One place for HR operations</span><span><Check size={15}/> Insights for better decisions</span></div></div><div className="ws-login-bottom">WORKSPHERE · HR MANAGEMENT SYSTEM</div></div><div className="ws-login-form-wrap"><form className="ws-login-form" onSubmit={submit}><span className="ws-eyebrow">WELCOME TO WORKSPHERE</span><h2>{register ? "Create your account" : "Sign in to your workspace"}</h2><p>{register ? "New accounts receive the Employee role." : "Use your company account to continue."}</p>{error && <div className="ws-alert error">{error}</div>}{register && <><label>Full name</label><input required minLength={1} value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Your full name"/><label>Email address</label><input required type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com"/></>}<label>Username</label><input required minLength={3} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" placeholder="Your username"/><label>Password</label><input required minLength={register ? 10 : 1} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={register ? "new-password" : "current-password"} placeholder={register ? "At least 10 characters" : "Enter your password"}/><button className="ws-primary ws-full" type="submit">{register ? "Create account" : "Sign in securely"}<ChevronRight size={17}/></button><div className="ws-login-switch">{register ? "Already registered?" : "New to WorkSphere?"}<button type="button" onClick={() => setRegister(!register)}>{register ? "Sign in" : "Create an account"}</button></div><div className="ws-login-secure"><ShieldCheck size={15}/> Your access is protected by role-based permissions.</div></form></div></div>;
}
function Overview({ summary, user, onNavigate }) {
    const stats = user.role === "EMPLOYEE" ? [
        { label: "My attendance today", value: Number(summary.present_today) ? "Recorded" : "Not marked", icon: <Clock3 />, tone: "blue" },
        { label: "Pending leave requests", value: String(summary.pending_leaves ?? 0), icon: <CalendarDays />, tone: "orange" },
        { label: "Current month payroll", value: money(summary.current_month_payroll), icon: <Wallet />, tone: "green" },
    ] : [
        { label: "Active employees", value: String(summary.total_employees ?? 0), icon: <Users />, tone: "purple" },
        { label: "Present today", value: String(summary.present_today ?? 0), icon: <Clock3 />, tone: "blue" },
        { label: "Pending leave requests", value: String(summary.pending_leaves ?? 0), icon: <CalendarDays />, tone: "orange" },
        { label: "Current month payroll", value: money(summary.current_month_payroll), icon: <Wallet />, tone: "green" },
    ];
    const departments = Array.isArray(summary.department_counts) ? summary.department_counts : [];
    return <div className="ws-page-stack"><section className="ws-welcome"><div><span className="ws-welcome-tag"><span /> YOUR WORKSPACE</span><h2>Good work starts<br />with <em>good people.</em></h2><p>Welcome back, {user.username}. Here is your WorkSphere overview.</p></div><div className="ws-welcome-art"><div className="ws-orbit orbit-one"/><div className="ws-orbit orbit-two"/><BriefcaseBusiness size={48}/></div></section><section className="ws-stat-grid">{stats.map((s) => <div className="ws-stat-card" key={s.label}><div className={`ws-stat-icon ${s.tone}`}>{s.icon}</div><span>{s.label}</span><strong>{s.value}</strong><small>{s.label.includes("payroll") ? "Net payroll recorded" : "Updated from live records"}</small></div>)}</section><section className="ws-panel"><div className="ws-panel-head"><div><h3>Quick actions</h3><p>Jump into the workflows you use most.</p></div></div><div className="ws-quick-grid"><QuickAction icon={<Clock3 />} title="Attendance" text="Check in, check out and history" onClick={() => onNavigate("Attendance")}/><QuickAction icon={<CalendarDays />} title="Leave management" text="Apply for leave and view status" onClick={() => onNavigate("Leave management")}/><QuickAction icon={<Wallet />} title="Payroll" text="Review salary and payroll history" onClick={() => onNavigate("Payroll")}/><QuickAction icon={<Bell />} title="Notifications" text="Review updates and alerts" onClick={() => onNavigate("Notifications")}/>{user.role !== "EMPLOYEE" && <QuickAction icon={<ChartNoAxesCombined />} title="Analytics" text="Explore HR trends and reports" onClick={() => onNavigate("Analytics")}/>}</div></section>{user.role !== "EMPLOYEE" && <section className="ws-panel"><div className="ws-panel-head"><div><h3>Department distribution</h3><p>Active employees by department</p></div></div>{departments.length ? <div className="ws-bars">{departments.map((row) => { const max = Math.max(1, ...departments.map((x) => Number(x.count))); return <div className="ws-bar-row" key={String(row.department)}><span>{String(row.department)}</span><div><i style={{ width: `${Number(row.count) / max * 100}%` }}/></div><b>{String(row.count)}</b></div>; })}</div> : <Empty message="Department analytics will appear when employee records exist."/>}</section>}</div>;
}
function QuickAction({ icon, title, text, onClick }) { return <button className="ws-quick-action" onClick={onClick}><span>{icon}</span><b>{title}</b><small>{text}</small><ChevronRight size={16}/></button>; }
function ProfilePage({ rows }) {
    const profile = rows[0];
    if (!profile)
        return <Empty message="Your employee profile is not available."/>;
    return <div className="ws-page-stack"><Toolbar title="My employee profile" description="Personal and employment information associated with your account."/><section className="ws-panel ws-profile-card"><div className="ws-profile-avatar">{String(profile.full_name ?? "U").slice(0, 1).toUpperCase()}</div><div><h2>{String(profile.full_name ?? "—")}</h2><p>{String(profile.designation ?? "Employee")} · {String(profile.department ?? "Unassigned")}</p><span className="ws-role">{String(profile.role ?? "EMPLOYEE")}</span></div></section><DataTable rows={rows} empty="Profile details unavailable." columns={[{ key: "full_name", label: "FULL NAME" }, { key: "username", label: "USERNAME" }, { key: "email", label: "EMAIL" }, { key: "designation", label: "DESIGNATION" }, { key: "department", label: "DEPARTMENT" }, { key: "joining_date", label: "JOINING DATE", format: dateLabel }, { key: "salary", label: "ANNUAL SALARY", format: money }, { key: "status", label: "EMPLOYMENT STATUS" }]}/></div>;
}
function EmployeesPage({ rows, search, setSearch, department, setDepartment, statusFilter, setStatusFilter, roleFilter, setRoleFilter, action, }) {
    const [show, setShow] = useState(false);
    const [form, setForm] = useState({
        username: "",
        email: "",
        password: "",
        full_name: "",
        designation: "Employee",
        department: "Unassigned",
        joining_date: new Date().toISOString().slice(0, 10),
        salary: "0",
    });
    const [editingEmployee, setEditingEmployee] = useState(null);
    const [editForm, setEditForm] = useState({
        full_name: "",
        department: "",
        designation: "",
        salary: "0",
        status: "ACTIVE",
    });
    const [savingEdit, setSavingEdit] = useState(false);
    const [editError, setEditError] = useState("");
    function submit(e) {
        e.preventDefault();
        void action("/employees/onboard", "POST", { ...form, salary: Number(form.salary) }, "Employee account created.");
        setShow(false);
    }
    function openEditModal(row) {
        setEditError("");
        setEditForm({
            full_name: String(row.full_name ?? ""),
            department: String(row.department ?? ""),
            designation: String(row.designation ?? ""),
            salary: String(row.salary ?? 0),
            status: String(row.status ?? "ACTIVE").toUpperCase(),
        });
        setEditingEmployee(row);
    }
    async function saveEmployee(e) {
        e.preventDefault();
        if (!editingEmployee)
            return;
        const salary = Number(editForm.salary);
        if (!Number.isFinite(salary) || salary < 0) {
            setEditError("Enter a valid, non-negative salary.");
            return;
        }
        if (!["ACTIVE", "INACTIVE"].includes(editForm.status)) {
            setEditError("Select a valid employment status.");
            return;
        }
        setSavingEdit(true);
        setEditError("");
        try {
            await action(`/employees/${editingEmployee.id}`, "PATCH", {
                full_name: editForm.full_name.trim(),
                department: editForm.department.trim(),
                designation: editForm.designation.trim(),
                salary,
                status: editForm.status,
            }, "Employee details updated.");
            setEditingEmployee(null);
        }
        catch (err) {
            setEditError(err instanceof Error ? err.message : "Could not update employee.");
        }
        finally {
            setSavingEdit(false);
        }
    }
    return (<div className="ws-page-stack">
      <Toolbar title="Employee directory" description={`${rows.length} employee record(s) loaded`}>
        <button className="ws-primary" type="button" onClick={() => setShow(!show)}>
          <Users size={16}/>
          {show ? "Close form" : "Add employee"}
        </button>
      </Toolbar>

      {show && (<form className="ws-form-grid ws-panel" onSubmit={submit}>
          <Field label="Full name">
            <input required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })}/>
          </Field>

          <Field label="Username">
            <input required minLength={3} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })}/>
          </Field>

          <Field label="Email">
            <input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })}/>
          </Field>

          <Field label="Temporary password">
            <input type="password" required minLength={10} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })}/>
          </Field>

          <Field label="Designation">
            <input value={form.designation} onChange={(e) => setForm({ ...form, designation: e.target.value })}/>
          </Field>

          <Field label="Department">
            <input value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })}/>
          </Field>

          <Field label="Joining date">
            <input type="date" value={form.joining_date} onChange={(e) => setForm({ ...form, joining_date: e.target.value })}/>
          </Field>

          <Field label="Annual salary (INR)">
            <input type="number" min="0" step="0.01" value={form.salary} onChange={(e) => setForm({ ...form, salary: e.target.value })}/>
          </Field>

          <button className="ws-primary" type="submit">
            Create employee
          </button>
        </form>)}

      <div className="ws-filterbar">
        <div className="ws-search">
          <Search size={16}/>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name, team, designation"/>
        </div>

        <input aria-label="Department filter" value={department} onChange={(e) => setDepartment(e.target.value)} placeholder="Filter department"/>

        <select aria-label="Employment status filter" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="ACTIVE">ACTIVE</option>
          <option value="INACTIVE">INACTIVE</option>
        </select>

        <select aria-label="Role filter" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
          <option value="">All roles</option>
          <option value="EMPLOYEE">EMPLOYEE</option>
          <option value="HR">HR</option>
          <option value="ADMIN">ADMIN</option>
        </select>
      </div>

      <DataTable rows={rows} empty="No employees found." columns={[
            { key: "full_name", label: "EMPLOYEE" },
            { key: "username", label: "USERNAME" },
            { key: "email", label: "EMAIL" },
            { key: "department", label: "DEPARTMENT" },
            { key: "designation", label: "DESIGNATION" },
            { key: "joining_date", label: "JOINED", format: dateLabel },
            { key: "salary", label: "ANNUAL SALARY", format: money },
            { key: "status", label: "STATUS" },
        ]} renderActions={(row) => (<button type="button" className="ws-mini-button" onClick={() => openEditModal(row)}>
            Edit
          </button>)}/>

      {editingEmployee && (<div className="ws-modal-overlay" onMouseDown={(e) => {
                if (e.target === e.currentTarget && !savingEdit) {
                    setEditingEmployee(null);
                }
            }}>
          <section className="ws-modal" role="dialog" aria-modal="true" aria-labelledby="ws-edit-title">
            <header className="ws-modal-header">
              <div>
                <span className="ws-eyebrow">EMPLOYEE DIRECTORY</span>
                <h2 id="ws-edit-title">Edit employee</h2>
                <p>
                  Update the employment details for{" "}
                  <strong>
                    {String(editingEmployee.full_name ?? "this employee")}
                  </strong>.
                </p>
              </div>

              <button type="button" className="ws-modal-close" aria-label="Close edit window" disabled={savingEdit} onClick={() => setEditingEmployee(null)}>
                <X size={20}/>
              </button>
            </header>

            <form className="ws-modal-form" onSubmit={saveEmployee}>
              {editError && (<div className="ws-modal-error" role="alert">
                  <X size={16}/>
                  {editError}
                </div>)}

              <Field label="Full name">
                <input required autoFocus value={editForm.full_name} onChange={(e) => setEditForm({
                ...editForm,
                full_name: e.target.value,
            })}/>
              </Field>

              <Field label="Department">
                <input value={editForm.department} onChange={(e) => setEditForm({
                ...editForm,
                department: e.target.value,
            })}/>
              </Field>

              <Field label="Designation">
                <input value={editForm.designation} onChange={(e) => setEditForm({
                ...editForm,
                designation: e.target.value,
            })}/>
              </Field>

              <Field label="Annual salary (INR)">
                <input type="number" required min="0" step="0.01" value={editForm.salary} onChange={(e) => setEditForm({
                ...editForm,
                salary: e.target.value,
            })}/>
              </Field>

              <Field label="Employment status">
                <select value={editForm.status} onChange={(e) => setEditForm({
                ...editForm,
                status: e.target.value,
            })}>
                  <option value="ACTIVE">ACTIVE</option>
                  <option value="INACTIVE">INACTIVE</option>
                </select>
              </Field>

              <footer className="ws-modal-actions">
                <button type="button" className="ws-secondary" disabled={savingEdit} onClick={() => setEditingEmployee(null)}>
                  Cancel
                </button>

                <button className="ws-primary" type="submit" disabled={savingEdit}>
                  {savingEdit ? "Saving..." : "Save changes"}
                </button>
              </footer>
            </form>
          </section>
        </div>)}
    </div>);
}
function AttendancePage({ rows, user, action, startDate, setStartDate, endDate, setEndDate }) {
    const today = new Date().toISOString().slice(0, 10);
    const todayRow = rows.find((x) => x.date === today && x.employee_id !== undefined);
    return <div className="ws-page-stack"><Toolbar title="Attendance tracking" description="Daily check-in, check-out, late entry and working hours.">{user.role !== "EMPLOYEE" && <button className="ws-secondary" onClick={() => void action("/attendance/automation/run", "POST", undefined, "Attendance automation completed for yesterday.")}>Run absence automation</button>}</Toolbar>{user.role === "EMPLOYEE" && <section className="ws-panel ws-clock-panel"><div><span className="ws-eyebrow">TODAY'S WORKDAY</span><h2>{todayRow ? todayRow.check_out ? "Workday completed" : "You are checked in" : "Ready to start?"}</h2><p>{todayRow ? `Check-in: ${dateLabel(todayRow.check_in)} · ${todayRow.worked_hours ?? "In progress"} hours recorded` : "Mark your attendance when you begin work."}</p></div><div className="ws-clock-actions">{!todayRow && <button className="ws-primary" onClick={() => void action("/attendance/check-in", "POST", undefined, "Attendance checked in.")}>Check in</button>}{todayRow && !todayRow.check_out && <button className="ws-primary" onClick={() => void action("/attendance/check-out", "POST", undefined, "Attendance checked out.")}>Check out</button>}</div></section>}<div className="ws-filterbar"><Field label="From"><input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}/></Field><Field label="To"><input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}/></Field></div><DataTable rows={rows} empty="No attendance records found for this period." columns={[{ key: "employee_name", label: "EMPLOYEE" }, { key: "date", label: "DATE", format: dateLabel }, { key: "check_in", label: "CHECK IN", format: (v) => v ? new Date(String(v)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—" }, { key: "check_out", label: "CHECK OUT", format: (v) => v ? new Date(String(v)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—" }, { key: "worked_hours", label: "HOURS" }, { key: "late_minutes", label: "LATE (MIN)" }, { key: "early_checkin_minutes", label: "EARLY IN (MIN)" }, { key: "early_checkout_minutes", label: "EARLY OUT (MIN)" }, { key: "overtime_hours", label: "OVERTIME" }, { key: "status", label: "STATUS" }]}/></div>;
}
function LeavePage({ rows, user, action }) {
    const [form, setForm] = useState({ leave_type: "CASUAL", start_date: new Date().toISOString().slice(0, 10), end_date: new Date().toISOString().slice(0, 10), reason: "" });
    const balances = useMemo(() => { try {
        return JSON.parse(sessionStorage.getItem("worksphere_leave_balances") || "[]");
    }
    catch {
        return [];
    } }, [rows]);
    return <div className="ws-page-stack"><Toolbar title="Leave management" description="Apply, track, review and manage employee leave."/>{user.role === "EMPLOYEE" && <form className="ws-panel ws-form-grid" onSubmit={(e) => { e.preventDefault(); void action("/leaves", "POST", form, "Leave request submitted."); }}><h3 className="ws-form-title">Apply for leave</h3><Field label="Leave type"><select value={form.leave_type} onChange={(e) => setForm({ ...form, leave_type: e.target.value })}><option>CASUAL</option><option>SICK</option><option>PAID</option></select></Field><Field label="Start date"><input type="date" required value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })}/></Field><Field label="End date"><input type="date" required min={form.start_date} value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })}/></Field><Field label="Reason"><input required value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Reason for leave"/></Field><button className="ws-primary" type="submit">Submit request</button></form>}{balances.length > 0 && <section className="ws-stat-grid ws-balance-grid">{balances.map((b) => <div className="ws-stat-card" key={`${b.employee_id}-${b.leave_type}`}><span>{String(b.leave_type)} balance</span><strong>{String(b.remaining_days)} days</strong><small>{user.role === "EMPLOYEE" ? "Available to you" : `Employee ID ${b.employee_id}`}</small></div>)}</section>}<DataTable rows={rows} empty="No leave requests found." columns={[{ key: "employee_name", label: "EMPLOYEE" }, { key: "leave_type", label: "TYPE" }, { key: "start_date", label: "FROM", format: dateLabel }, { key: "end_date", label: "TO", format: dateLabel }, { key: "reason", label: "REASON" }, { key: "status", label: "STATUS" }, { key: "created_at", label: "REQUESTED", format: dateLabel }]} renderActions={(row) => user.role !== "EMPLOYEE" && row.status === "PENDING" ? <div className="ws-row-actions"><button className="ws-mini-button good" onClick={() => void action(`/leaves/${row.id}/decision`, "PATCH", { status: "APPROVED" }, "Leave approved and balance updated.")}>Approve</button><button className="ws-mini-button danger" onClick={() => void action(`/leaves/${row.id}/decision`, "PATCH", { status: "REJECTED" }, "Leave rejected.")}>Reject</button></div> : null}/></div>;
}
function PayrollPage({ rows, user, employees, month, setMonth, action, onDownload }) {
    const [form, setForm] = useState({ employee_id: "", basic_salary: "", allowances: "0", deductions: "0", tax_deduction: "0", bonus: "0" });
    return <div className="ws-page-stack"><Toolbar title="Payroll & payslips" description="Review salary components, net pay and payroll history.">{user.role !== "EMPLOYEE" && <button className="ws-secondary" onClick={() => void action(`/payroll/generate-month?month=${month}`, "POST", undefined, `Payroll generation completed for ${month}.`)}>Generate payroll for month</button>}</Toolbar><div className="ws-filterbar"><Field label="Payroll month"><input type="month" value={month} onChange={(e) => setMonth(e.target.value)}/></Field></div>{user.role !== "EMPLOYEE" && <form className="ws-panel ws-form-grid" onSubmit={(e) => { e.preventDefault(); void action("/payroll", "POST", { ...form, employee_id: Number(form.employee_id), month, basic_salary: Number(form.basic_salary), allowances: Number(form.allowances), deductions: Number(form.deductions), tax_deduction: Number(form.tax_deduction), bonus: Number(form.bonus) }, "Payroll record created."); }}><h3 className="ws-form-title">Create individual payroll record</h3><Field label="Employee"><select required value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })}><option value="">Select employee</option>{employees.map((e) => <option key={String(e.id)} value={String(e.id)}>{String(e.full_name)} · #{String(e.id)}</option>)}</select></Field>{["basic_salary", "allowances", "deductions", "tax_deduction", "bonus"].map((key) => <Field key={key} label={key.replace(/_/g, " ")}><input type="number" min="0" step="0.01" required value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })}/></Field>)}<button className="ws-primary" type="submit">Save payroll</button></form>}<DataTable rows={rows} empty="No payroll records found for this month." columns={[{ key: "employee_name", label: "EMPLOYEE" }, { key: "month", label: "MONTH" }, { key: "annual_salary", label: "ANNUAL SALARY", format: money }, { key: "basic_salary", label: "BASIC", format: money }, { key: "allowances", label: "ALLOWANCES", format: money }, { key: "bonus", label: "BONUS", format: money }, { key: "deductions", label: "DEDUCTIONS", format: money }, { key: "tax_deduction", label: "TAX", format: money }, { key: "net_salary", label: "NET PAY", format: money }]} renderActions={(row) => <button className="ws-mini-button" onClick={() => void onDownload(row)}>Download payslip</button>}/></div>;
}
function NotificationsPage({ rows, action }) { return <div className="ws-page-stack"><Toolbar title="Notifications" description={`${rows.filter((x) => !x.is_read).length} unread notification(s)`}><button className="ws-secondary" onClick={() => void action("/notifications/read-all", "POST", undefined, "All notifications marked as read.")}>Mark all read</button></Toolbar><div className="ws-notification-list">{rows.map((row) => <article className={`ws-notification ${row.is_read ? "read" : "unread"}`} key={String(row.id)}><span className="ws-notification-icon"><Bell size={18}/></span><div><b>{String(row.title)}</b><p>{String(row.message)}</p><small>{dateLabel(row.created_at)}</small></div>{!row.is_read && <button className="ws-mini-button" onClick={() => void action(`/notifications/${row.id}/read`, "PATCH", undefined, "Notification marked as read.")}>Mark read</button>}</article>)}{!rows.length && <Empty message="You're all caught up. Notifications will appear here."/>}</div></div>; }
function UsersPage({ rows, search, setSearch, action }) {
    const [form, setForm] = useState({ username: "", email: "", password: "", full_name: "", role: "EMPLOYEE", designation: "Employee", department: "Unassigned", salary: "0" });
    return <div className="ws-page-stack"><Toolbar title="User administration" description="Manage accounts, roles and account status."/><form className="ws-panel ws-form-grid" onSubmit={(e) => { e.preventDefault(); void action("/admin/users", "POST", { ...form, salary: Number(form.salary), joining_date: new Date().toISOString().slice(0, 10) }, "User account created."); }}><h3 className="ws-form-title">Create user and employee profile</h3><Field label="Full name"><input required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })}/></Field><Field label="Username"><input required minLength={3} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })}/></Field><Field label="Email"><input required type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })}/></Field><Field label="Temporary password"><input required type="password" minLength={10} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })}/></Field><Field label="Role"><select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option>EMPLOYEE</option><option>HR</option><option>ADMIN</option></select></Field><Field label="Department"><input value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })}/></Field><Field label="Designation"><input value={form.designation} onChange={(e) => setForm({ ...form, designation: e.target.value })}/></Field><Field label="Annual salary"><input type="number" min="0" value={form.salary} onChange={(e) => setForm({ ...form, salary: e.target.value })}/></Field><button className="ws-primary" type="submit">Create user</button></form><div className="ws-filterbar"><div className="ws-search"><Search size={16}/><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search users"/></div></div><DataTable rows={rows} empty="No users found." columns={[{ key: "id", label: "ID" }, { key: "username", label: "USERNAME" }, { key: "email", label: "EMAIL" }, { key: "role", label: "ROLE" }, { key: "is_active", label: "ACTIVE", format: (v) => v ? "Active" : "Inactive" }, { key: "created_at", label: "CREATED", format: dateLabel }]} renderActions={(row) => <div className="ws-row-actions"><select aria-label={`Role for ${String(row.username)}`} value={String(row.role)} onChange={(e) => void action(`/admin/users/${row.id}`, "PATCH", { role: e.target.value }, "User role updated.")}><option>EMPLOYEE</option><option>HR</option><option>ADMIN</option></select><button className="ws-mini-button" onClick={() => void action(`/admin/users/${row.id}`, "PATCH", { is_active: !row.is_active }, `User ${row.is_active ? "deactivated" : "activated"}.`)}>{row.is_active ? "Deactivate" : "Activate"}</button><button className="ws-mini-button danger" onClick={() => { if (window.confirm(`Permanently delete ${String(row.username)}? Users with HR history must be deactivated instead.`))
        void action(`/admin/users/${row.id}`, "DELETE", undefined, "User permanently deleted."); }}>Delete</button></div>}/></div>;
}
function PoliciesPage({ rows, employees, action }) {
    const [type, setType] = useState("CASUAL");
    const [days, setDays] = useState("12");
    const [employeeId, setEmployeeId] = useState("");
    const [balanceType, setBalanceType] = useState("CASUAL");
    const [remaining, setRemaining] = useState("12");
    const balances = useMemo(() => { try {
        return JSON.parse(sessionStorage.getItem("worksphere_leave_balances") || "[]");
    }
    catch {
        return [];
    } }, [rows]);
    return <div className="ws-page-stack"><Toolbar title="Leave policies & balances" description="Configure annual entitlements and manage remaining days."/><form className="ws-panel ws-form-grid" onSubmit={(e) => { e.preventDefault(); void action("/leave-policies", "PUT", { leave_type: type, total_days_allowed: Number(days) }, "Leave policy saved."); }}><h3 className="ws-form-title">Set leave policy</h3><Field label="Leave type"><select value={type} onChange={(e) => setType(e.target.value)}><option>SICK</option><option>CASUAL</option><option>PAID</option></select></Field><Field label="Days allowed per year"><input type="number" min="0" max="365" value={days} onChange={(e) => setDays(e.target.value)}/></Field><button className="ws-primary" type="submit">Save policy</button></form><DataTable rows={rows} empty="No leave policies found." columns={[{ key: "leave_type", label: "LEAVE TYPE" }, { key: "total_days_allowed", label: "ANNUAL DAYS" }, { key: "updated_at", label: "LAST UPDATED", format: dateLabel }]}/><form className="ws-panel ws-form-grid" onSubmit={(e) => { e.preventDefault(); void action("/leave-balances", "PUT", { employee_id: Number(employeeId), leave_type: balanceType, remaining_days: Number(remaining) }, "Employee leave balance updated."); }}><h3 className="ws-form-title">Adjust employee balance</h3><Field label="Employee"><select required value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}><option value="">Select employee</option>{employees.map((x) => <option key={String(x.id)} value={String(x.id)}>{String(x.full_name)} · #{String(x.id)}</option>)}</select></Field><Field label="Leave type"><select value={balanceType} onChange={(e) => setBalanceType(e.target.value)}><option>SICK</option><option>CASUAL</option><option>PAID</option></select></Field><Field label="Remaining days"><input type="number" min="0" max="365" value={remaining} onChange={(e) => setRemaining(e.target.value)}/></Field><button className="ws-primary" type="submit">Update balance</button></form><DataTable rows={balances} empty="No leave balances found." columns={[{ key: "employee_id", label: "EMPLOYEE ID" }, { key: "leave_type", label: "LEAVE TYPE" }, { key: "remaining_days", label: "REMAINING DAYS" }]}/></div>;
}
function ShiftsPage({ rows, shifts, employees, action }) {
    const [shift, setShift] = useState({ name: "Morning", start_time: "09:00", end_time: "17:00", grace_minutes: "10", expected_hours: "8" });
    const [assignment, setAssignment] = useState({ employee_id: "", shift_id: "", effective_from: new Date().toISOString().slice(0, 10), effective_to: "" });
    return <div className="ws-page-stack"><Toolbar title="Shift management" description="Create work shifts and assign schedules to employees."/><form className="ws-panel ws-form-grid" onSubmit={(e) => { e.preventDefault(); void action("/shifts", "POST", { ...shift, grace_minutes: Number(shift.grace_minutes), expected_hours: Number(shift.expected_hours) }, "Shift created."); }}><h3 className="ws-form-title">Create shift</h3><Field label="Shift name"><input required value={shift.name} onChange={(e) => setShift({ ...shift, name: e.target.value })}/></Field><Field label="Start time"><input required type="time" value={shift.start_time} onChange={(e) => setShift({ ...shift, start_time: e.target.value })}/></Field><Field label="End time"><input required type="time" value={shift.end_time} onChange={(e) => setShift({ ...shift, end_time: e.target.value })}/></Field><Field label="Grace minutes"><input type="number" min="0" value={shift.grace_minutes} onChange={(e) => setShift({ ...shift, grace_minutes: e.target.value })}/></Field><Field label="Expected hours"><input type="number" min="1" max="24" step="0.5" value={shift.expected_hours} onChange={(e) => setShift({ ...shift, expected_hours: e.target.value })}/></Field><button className="ws-primary" type="submit">Save shift</button></form><DataTable rows={shifts} empty="No shifts created yet." columns={[{ key: "name", label: "SHIFT" }, { key: "start_time", label: "START" }, { key: "end_time", label: "END" }, { key: "grace_minutes", label: "GRACE (MIN)" }, { key: "expected_hours", label: "EXPECTED HOURS" }]}/><form className="ws-panel ws-form-grid" onSubmit={(e) => { e.preventDefault(); void action("/shift-assignments", "POST", { employee_id: Number(assignment.employee_id), shift_id: Number(assignment.shift_id), effective_from: assignment.effective_from, effective_to: assignment.effective_to || null }, "Shift assigned to employee."); }}><h3 className="ws-form-title">Assign shift</h3><Field label="Employee"><select required value={assignment.employee_id} onChange={(e) => setAssignment({ ...assignment, employee_id: e.target.value })}><option value="">Select employee</option>{employees.map((x) => <option key={String(x.id)} value={String(x.id)}>{String(x.full_name)} · #{String(x.id)}</option>)}</select></Field><Field label="Shift"><select required value={assignment.shift_id} onChange={(e) => setAssignment({ ...assignment, shift_id: e.target.value })}><option value="">Select shift</option>{shifts.map((x) => <option key={String(x.id)} value={String(x.id)}>{String(x.name)}</option>)}</select></Field><Field label="Effective from"><input type="date" required value={assignment.effective_from} onChange={(e) => setAssignment({ ...assignment, effective_from: e.target.value })}/></Field><Field label="Effective to (optional)"><input type="date" value={assignment.effective_to} onChange={(e) => setAssignment({ ...assignment, effective_to: e.target.value })}/></Field><button className="ws-primary" type="submit">Assign shift</button></form><DataTable rows={rows} empty="No employee shifts assigned yet." columns={[{ key: "employee_name", label: "EMPLOYEE" }, { key: "shift_name", label: "SHIFT" }, { key: "start_time", label: "START" }, { key: "end_time", label: "END" }, { key: "effective_from", label: "FROM", format: dateLabel }, { key: "effective_to", label: "TO", format: dateLabel }]}/></div>;
}
function CalendarPage({ rows, startDate, setStartDate, endDate, setEndDate, department, setDepartment }) { return <div className="ws-page-stack"><Toolbar title="Team leave calendar" description="Approved leave across the selected date range."/><div className="ws-filterbar"><Field label="From"><input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}/></Field><Field label="To"><input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}/></Field><Field label="Department"><input value={department} onChange={(e) => setDepartment(e.target.value)} placeholder="All departments"/></Field></div><div className="ws-calendar-grid">{rows.map((row) => <article className="ws-calendar-event" key={String(row.id)}><span>{String(row.leave_type)}</span><b>{String(row.employee_name)}</b><small>{String(row.department ?? "Team")}</small><p>{dateLabel(row.start_date)} – {dateLabel(row.end_date)}</p></article>)}{!rows.length && <Empty message="No approved leave in this period."/>}</div></div>; }
function DocumentsPage({ rows, user, employees, onUpload, onDownload, action }) {
    const [employeeId, setEmployeeId] = useState("");
    const [expiresOn, setExpiresOn] = useState("");
    const [file, setFile] = useState(null);
    function submit(e) { e.preventDefault(); if (!file)
        return; const body = new FormData(); body.append("employee_id", employeeId || String(employees[0]?.id ?? "")); if (expiresOn)
        body.append("expires_on", expiresOn); body.append("file", file); void onUpload(body); }
    return <div className="ws-page-stack"><Toolbar title="Employee documents" description="Upload and securely access employee documents (maximum 10 MB)."/><form className="ws-panel ws-form-grid" onSubmit={submit}><h3 className="ws-form-title">Upload document</h3>{user.role !== "EMPLOYEE" && <Field label="Employee"><select required value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}><option value="">Select employee</option>{employees.map((x) => <option key={String(x.id)} value={String(x.id)}>{String(x.full_name)} · #{String(x.id)}</option>)}</select></Field>}<Field label="Expiry date (optional)"><input type="date" value={expiresOn} onChange={(e) => setExpiresOn(e.target.value)}/></Field><Field label="File (PDF, image, DOC/DOCX or TXT)"><input type="file" required accept=".pdf,.png,.jpg,.jpeg,.doc,.docx,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)}/></Field><button className="ws-primary" type="submit">Upload document</button></form><DataTable rows={rows} empty="No documents uploaded." columns={[{ key: "id", label: "ID" }, { key: "employee_id", label: "EMPLOYEE ID" }, { key: "file_name", label: "FILE NAME" }, { key: "content_type", label: "TYPE" }, { key: "expires_on", label: "EXPIRES", format: dateLabel }, { key: "uploaded_at", label: "UPLOADED", format: dateLabel }]} renderActions={(row) => <div className="ws-row-actions"><button className="ws-mini-button" onClick={() => void onDownload(row)}>Download</button>{user.role !== "EMPLOYEE" && <button className="ws-mini-button danger" onClick={() => void action(`/documents/${row.id}`, "DELETE", undefined, "Document deleted.")}>Delete</button>}</div>}/></div>;
}
function PerformancePage({ rows, user, employees, action }) {
    const [form, setForm] = useState({ employee_id: "", review_period: new Date().getFullYear().toString(), rating: "4", feedback: "" });
    return <div className="ws-page-stack"><Toolbar title="Performance reviews" description="Track ratings, review periods and manager feedback."/>{user.role !== "EMPLOYEE" && <form className="ws-panel ws-form-grid" onSubmit={(e) => { e.preventDefault(); void action("/performance", "POST", { ...form, employee_id: Number(form.employee_id), rating: Number(form.rating) }, "Performance review saved."); }}><h3 className="ws-form-title">Create review</h3><Field label="Employee"><select required value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value })}><option value="">Select employee</option>{employees.map((x) => <option key={String(x.id)} value={String(x.id)}>{String(x.full_name)} · #{String(x.id)}</option>)}</select></Field><Field label="Review period"><input required value={form.review_period} onChange={(e) => setForm({ ...form, review_period: e.target.value })} placeholder="2026 Q4"/></Field><Field label="Rating (1–5)"><input type="number" min="1" max="5" step="0.5" value={form.rating} onChange={(e) => setForm({ ...form, rating: e.target.value })}/></Field><Field label="Feedback"><textarea required value={form.feedback} onChange={(e) => setForm({ ...form, feedback: e.target.value })}/></Field><button className="ws-primary" type="submit">Save review</button></form>}<DataTable rows={rows} empty="No performance reviews found." columns={[{ key: "employee_name", label: "EMPLOYEE" }, { key: "review_period", label: "PERIOD" }, { key: "rating", label: "RATING" }, { key: "reviewer_username", label: "REVIEWER" }, { key: "feedback", label: "FEEDBACK" }, { key: "created_at", label: "CREATED", format: dateLabel }]}/></div>;
}
function AnalyticsPage({ report, startDate, setStartDate, endDate, setEndDate, department, setDepartment }) {
    const attendance = (report.attendance_by_status ?? {});
    const leaves = (report.leave_by_status ?? {});
    const leaveTypes = (report.leave_by_type ?? {});
    return <div className="ws-page-stack"><Toolbar title="HR analytics & reports" description="Attendance trends, leave patterns and performance summaries."/><div className="ws-filterbar"><Field label="From"><input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}/></Field><Field label="To"><input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}/></Field><Field label="Department"><input value={department} onChange={(e) => setDepartment(e.target.value)} placeholder="All departments"/></Field></div><div className="ws-stat-grid"><MiniStat label="Attendance records" value={report.attendance_records ?? 0}/><MiniStat label="Leave requests" value={report.leave_requests ?? 0}/><MiniStat label="Performance reviews" value={report.performance_review_count ?? 0}/><MiniStat label="Average rating" value={report.average_performance_rating ?? "No reviews"}/></div><div className="ws-two-col"><MetricPanel title="Attendance trends" values={attendance}/><MetricPanel title="Leave status" values={leaves}/><MetricPanel title="Leave by type" values={leaveTypes}/></div></div>;
}
function AdvancedSearchPage({ rows, search, setSearch, department, setDepartment, statusFilter, setStatusFilter, roleFilter, setRoleFilter, attendanceFilter, setAttendanceFilter, minRating, setMinRating }) { return <div className="ws-page-stack"><Toolbar title="Advanced employee search" description="Filter by name, department, role, status, attendance and latest performance."/><div className="ws-filterbar"><div className="ws-search"><Search size={16}/><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name, username, email or designation"/></div><input value={department} onChange={(e) => setDepartment(e.target.value)} placeholder="Department"/><select aria-label="Status filter" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">All statuses</option><option>ACTIVE</option><option>INACTIVE</option></select><select aria-label="Role filter" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}><option value="">All roles</option><option>EMPLOYEE</option><option>HR</option><option>ADMIN</option></select><select aria-label="Today's attendance filter" value={attendanceFilter} onChange={(e) => setAttendanceFilter(e.target.value)}><option value="">All attendance</option><option>PRESENT</option><option>LATE</option><option>ABSENT</option><option>HALF_DAY</option><option>NOT_MARKED</option></select><select aria-label="Minimum performance rating" value={minRating} onChange={(e) => setMinRating(e.target.value)}><option value="">Any rating</option><option value="1">1+</option><option value="2">2+</option><option value="3">3+</option><option value="4">4+</option><option value="5">5</option></select></div><DataTable rows={rows} empty="No matching employees found." columns={[{ key: "full_name", label: "EMPLOYEE" }, { key: "username", label: "USERNAME" }, { key: "department", label: "DEPARTMENT" }, { key: "designation", label: "DESIGNATION" }, { key: "role", label: "ROLE" }, { key: "status", label: "STATUS" }, { key: "today_attendance", label: "TODAY" }, { key: "latest_rating", label: "LATEST RATING" }]}/></div>; }
function Toolbar({ title, description, children }) { return <div className="ws-page-toolbar"><div><h2>{title}</h2><p>{description}</p></div>{children && <div className="ws-toolbar-actions">{children}</div>}</div>; }
function Field({ label, children }) { return <label className="ws-field"><span>{label}</span>{children}</label>; }
function Empty({ message }) { return <div className="ws-empty"><span><FileText size={23}/></span><b>Nothing to show yet</b><p>{message}</p></div>; }
function DataTable({ rows, columns, empty, renderActions }) { return <section className="ws-panel ws-table-panel">{rows.length ? <div className="ws-table-scroll"><table className="ws-table"><thead><tr>{columns.map((c) => <th key={c.key}>{c.label}</th>)}{renderActions && <th>ACTIONS</th>}</tr></thead><tbody>{rows.map((row, i) => <tr key={String(row.id ?? `${row.employee_id}-${i}`)}>{columns.map((c) => <td key={c.key}>{c.format ? c.format(row[c.key]) : String(row[c.key] ?? "—")}</td>)}{renderActions && <td>{renderActions(row)}</td>}</tr>)}</tbody></table></div> : <Empty message={empty}/>}</section>; }
function MiniStat({ label, value }) { return <div className="ws-stat-card"><span>{label}</span><strong>{String(value)}</strong></div>; }
function MetricPanel({ title, values }) { const entries = Object.entries(values); const max = Math.max(1, ...entries.map(([, v]) => Number(v))); return <section className="ws-panel"><div className="ws-panel-head"><h3>{title}</h3></div>{entries.length ? <div className="ws-bars">{entries.map(([label, value]) => <div className="ws-bar-row" key={label}><span>{label.replace(/_/g, " ")}</span><div><i style={{ width: `${Number(value) / max * 100}%` }}/></div><b>{String(value)}</b></div>)}</div> : <Empty message="No data available for the selected filters."/>}</section>; }
export default App;
