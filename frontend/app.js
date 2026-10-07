"use strict";
const $app = document.getElementById("app");
const S = { token: localStorage.getItem("pm_token"), user: null, view: null };
const inr = (n) => "₹" + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const badge = (s) => h("span", { class: "badge " + s }, String(s).replace(/_/g, " "));

async function api(path, { method = "GET", body, form, raw } = {}) {
  const headers = {};
  if (S.token) headers.Authorization = "Bearer " + S.token;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch("/api" + path, { method, headers, body: payload });
  if (res.status === 401 && S.token && !path.startsWith("/auth/")) { logout(); throw new Error("Session expired"); }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : Array.isArray(j.detail) ? j.detail.map((d) => (d.loc || []).slice(1).join(".") + ": " + d.msg).join("; ") : msg;
    } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return raw ? res : res.json();
}

async function download(path, name) {
  try {
    const res = await api(path, { raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = h("a", { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (e) { flash(e.message, true); }
}

let flashEl = null;
function flash(text, err) {
  if (flashEl) flashEl.remove();
  flashEl = h("div", { class: "msg" + (err ? " err" : "") }, text);
  const main = document.querySelector(".main") || $app;
  main.prepend(flashEl);
  setTimeout(() => flashEl && flashEl.remove(), 6000);
}
const act = (fn) => async (...a) => { try { await fn(...a); } catch (e) { flash(e.message, true); } };

function logout() { localStorage.removeItem("pm_token"); S.token = null; S.user = null; render(); }

/* ---------- generic UI helpers ---------- */
function table(rows, cols) {
  if (!rows.length) return h("p", { class: "muted" }, "Nothing here yet.");
  return h("table", {},
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", {}, c.t)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, cols.map((c) => h("td", {}, c.f(r)))))));
}
function formCard(title, fields, onSubmit, submitLabel = "Save") {
  const inputs = {};
  const form = h("form", { class: "card", onsubmit: act(async (e) => {
    e.preventDefault();
    const out = {};
    for (const f of fields) {
      let v = inputs[f.n].value;
      if (v === "" && !f.req) continue;
      if (f.t === "number") v = Number(v);
      out[f.n] = v;
    }
    await onSubmit(out);
  }) }, h("h3", {}, title));
  for (const f of fields) {
    let inp;
    if (f.opts) inp = h("select", {}, f.opts.map((o) => h("option", { value: o.v ?? o }, o.l ?? o)));
    else if (f.t === "textarea") inp = h("textarea", { rows: 2 });
    else inp = h("input", { type: f.t || "text", step: f.t === "number" ? "any" : null });
    if (f.req) inp.required = true;
    if (f.v !== undefined) inp.value = f.v;
    inputs[f.n] = inp;
    form.append(h("label", {}, f.l), inp);
  }
  form.append(h("div", { class: "row", style: "margin-top:10px" }, h("button", { type: "submit" }, submitLabel)));
  return form;
}
const kpi = (label, value) => h("div", { class: "kpi" }, h("b", {}, value), h("span", {}, label));
const today = () => new Date().toISOString().slice(0, 10);
const plusDays = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
const thisMonth = () => today().slice(0, 7);

/* ---------- views ---------- */
const views = {
  async dashboard(box) {
    const d = await api("/dashboard");
    box.append(h("h2", {}, "Dashboard"));
    if (d.role === "landlord") {
      box.append(h("div", { class: "grid" }, kpi("Properties", d.properties), kpi("Units", d.units), kpi("Tenants", d.tenants),
        kpi("Rent due", inr(d.rent_due)), kpi("Collected", inr(d.collected)), kpi("Outstanding", inr(d.outstanding)),
        kpi("Overdue tenants", d.overdue_tenants), kpi("Collection rate", d.collection_rate + "%"), kpi("Occupancy", d.occupancy_rate + "%")));
      box.append(h("div", { class: "card" }, h("h3", {}, "Maintenance"),
        Object.keys(d.maintenance).length ? Object.entries(d.maintenance).map(([k, v]) => h("span", {}, badge(k), " " + v + "  ")) : h("span", { class: "muted" }, "No tickets")));
      const months = Object.entries(d.monthly_collection);
      box.append(h("div", { class: "card" }, h("h3", {}, "Monthly rent collection"),
        months.length ? months.map(([m, v]) => h("div", {}, h("div", { class: "row" }, m + "  " + inr(v.collected) + " / " + inr(v.due)),
          h("div", { class: "bar" }, h("i", { style: `width:${v.due ? Math.min(100, (v.collected / v.due) * 100) : 0}%` })))) : h("span", { class: "muted" }, "No invoices")));
      box.append(h("div", { class: "card" }, h("h3", {}, "Maintenance cost by category"),
        Object.keys(d.maintenance_cost_by_category).length ? Object.entries(d.maintenance_cost_by_category).map(([k, v]) => h("div", {}, k + ": " + inr(v))) : h("span", { class: "muted" }, "No costs recorded")));
      box.append(h("div", { class: "card" }, h("h3", {}, "Upcoming lease expirations"),
        table(d.upcoming_expirations, [{ t: "Unit", f: (r) => r.unit }, { t: "Ends", f: (r) => r.end_date }])));
    } else if (d.role === "tenant") {
      box.append(h("div", { class: "card" }, h("h3", {}, "My rental"),
        d.lease ? h("div", {}, `${d.lease.property} · Unit ${d.lease.unit} · ${inr(d.lease.monthly_rent)}/month · Lease ${d.lease.status} until ${d.lease.end_date}`) : h("span", { class: "muted" }, "No active lease yet.")),
        h("div", { class: "card" }, h("h3", {}, "Next payment"),
          d.next_due ? h("div", {}, `${d.next_due.period}: ${inr(d.next_due.outstanding)} due ${d.next_due.due_date} `, badge(d.next_due.status)) : h("span", { class: "muted" }, "Nothing due")),
        h("div", { class: "card" }, "Open maintenance tickets: " + d.open_tickets));
    } else if (d.role === "maintenance") {
      box.append(h("div", { class: "card" }, "Assigned tickets: " + d.assigned, h("div", {}, Object.entries(d.by_status).map(([k, v]) => h("span", {}, badge(k), " " + v + " ")))));
    } else {
      box.append(h("div", { class: "grid" }, kpi("Users", d.users), kpi("Landlords", d.landlords), kpi("Properties", d.properties), kpi("Audit events", d.audit_events)));
    }
  },

  async properties(box) {
    const rows = await api("/properties");
    box.append(h("h2", {}, "Properties"),
      formCard("Add property", [{ n: "name", l: "Name", req: 1 }, { n: "address", l: "Address", req: 1 },
        { n: "property_type", l: "Type", opts: ["apartment", "house", "commercial", "room"] }],
      async (b) => { await api("/properties", { method: "POST", body: b }); flash("Property added"); go("properties"); }, "Add"),
      h("div", { class: "card" }, table(rows, [{ t: "Name", f: (r) => r.name }, { t: "Address", f: (r) => r.address }, { t: "Type", f: (r) => r.property_type },
        { t: "Units", f: (r) => r.units_count },
        { t: "", f: (r) => h("button", { class: "sec", onclick: () => go("property", r.id) }, "Units") }])));
  },
  async property(box, id) {
    const p = await api("/properties/" + id);
    box.append(h("button", { class: "sec", onclick: () => go("properties") }, "← Back"), h("h2", {}, p.name), h("p", { class: "muted" }, p.address),
      formCard("Add unit", [{ n: "unit_number", l: "Unit number", req: 1 }, { n: "monthly_rent", l: "Monthly rent (₹)", t: "number", req: 1 },
        { n: "security_deposit", l: "Security deposit (₹)", t: "number" }, { n: "amenities", l: "Amenities" }],
      async (b) => { b.property_id = p.id; await api("/units", { method: "POST", body: b }); flash("Unit added"); go("property", id); }, "Add unit"),
      h("div", { class: "card" }, table(p.units, [{ t: "Unit", f: (r) => r.unit_number }, { t: "Rent", f: (r) => inr(r.monthly_rent) },
        { t: "Deposit", f: (r) => inr(r.security_deposit) }, { t: "Status", f: (r) => badge(r.status) }, { t: "Amenities", f: (r) => r.amenities }])));
  },

  async tenants(box) {
    const rows = await api("/tenants");
    box.append(h("h2", {}, "Tenants"),
      formCard("Invite tenant", [{ n: "name", l: "Name", req: 1 }, { n: "email", l: "Email", t: "email", req: 1 }, { n: "phone", l: "Phone" }],
        async (b) => { const r = await api("/tenants", { method: "POST", body: b }); alert(`Tenant created.\nLogin: ${r.email}\nTemporary password: ${r.temp_password}\n(Share securely; in production this is emailed.)`); go("tenants"); }, "Invite"),
      h("div", { class: "card" }, table(rows, [{ t: "Name", f: (r) => r.name }, { t: "Email", f: (r) => r.email }, { t: "Phone", f: (r) => r.phone },
        { t: "Unit", f: (r) => r.current_unit || "-" }, { t: "Lease end", f: (r) => r.lease_end || "-" }, { t: "Status", f: (r) => badge(r.status) }])));
  },

  async staff(box) {
    const rows = await api("/staff");
    box.append(h("h2", {}, "Maintenance staff"),
      formCard("Add staff", [{ n: "name", l: "Name", req: 1 }, { n: "email", l: "Email", t: "email", req: 1 }],
        async (b) => { const r = await api("/staff", { method: "POST", body: b }); alert(`Staff created.\nLogin: ${r.email}\nTemporary password: ${r.temp_password}`); go("staff"); }, "Add"),
      h("div", { class: "card" }, table(rows, [{ t: "Name", f: (r) => r.name }, { t: "Email", f: (r) => r.email }, { t: "Status", f: (r) => badge(r.status) }])));
  },

  async leases(box) {
    const rows = await api("/leases");
    box.append(h("h2", {}, "Leases"));
    if (S.user.role === "landlord") {
      const [units, tenants] = await Promise.all([api("/units"), api("/tenants")]);
      box.append(formCard("Create lease", [
        { n: "unit_id", l: "Unit", opts: units.map((u) => ({ v: u.id, l: `${u.property_name} – ${u.unit_number}` })), req: 1 },
        { n: "tenant_id", l: "Tenant", opts: tenants.map((t) => ({ v: t.id, l: t.name })), req: 1 },
        { n: "start_date", l: "Start", t: "date", v: today(), req: 1 }, { n: "end_date", l: "End", t: "date", v: plusDays(365), req: 1 },
        { n: "monthly_rent", l: "Monthly rent (₹)", t: "number", req: 1 }, { n: "deposit", l: "Deposit (₹)", t: "number" },
        { n: "due_day", l: "Due day (1-28)", t: "number", v: 5 }, { n: "late_fee_policy", l: "Late payment policy", t: "textarea" },
        { n: "maintenance_terms", l: "Maintenance responsibilities", t: "textarea" }, { n: "additional_terms", l: "Additional terms", t: "textarea" }],
      async (b) => { b.unit_id = +b.unit_id; b.tenant_id = +b.tenant_id; await api("/leases", { method: "POST", body: b }); flash("Lease drafted"); go("leases"); }, "Create draft"));
    }
    const doAct = (id, a, body) => act(async () => { await api(`/leases/${id}/${a}`, { method: "POST", body }); go("leases"); })();
    box.append(h("div", { class: "card" }, table(rows, [{ t: "#", f: (r) => r.id }, { t: "Unit", f: (r) => `${r.property_name} ${r.unit_number}` }, { t: "Tenant", f: (r) => r.tenant_name },
      { t: "Term", f: (r) => `${r.start_date} → ${r.end_date}` }, { t: "Rent", f: (r) => inr(r.monthly_rent) }, { t: "Status", f: (r) => badge(r.status) },
      { t: "Actions", f: (r) => h("span", {},
        S.user.role === "landlord" && r.status === "draft" && h("button", { onclick: () => doAct(r.id, "send") }, "Send"),
        S.user.role === "tenant" && r.status === "sent" && h("button", { onclick: () => doAct(r.id, "sign") }, "Sign"),
        S.user.role === "landlord" && r.status === "tenant_signed" && h("button", { onclick: () => doAct(r.id, "sign") }, "Countersign"),
        S.user.role === "landlord" && ["active", "expiring", "expired"].includes(r.status) && h("button", { class: "sec", onclick: act(async () => {
          const end = prompt("New end date (YYYY-MM-DD)", plusDays(730)); if (end) doAct(r.id, "renew", { end_date: end }); }) }, "Renew"),
        h("button", { class: "sec", onclick: () => download(`/leases/${r.id}/pdf`, `lease-${r.id}.pdf`) }, "PDF")) }])));
  },

  async rent(box) {
    const [rows, pays] = await Promise.all([api("/rent/invoices"), api("/payments")]);
    box.append(h("h2", {}, "Rent"));
    const L = S.user.role === "landlord";
    if (L) {
      const leases = (await api("/leases")).filter((l) => ["active", "expiring"].includes(l.status));
      box.append(formCard("Generate invoices for all active leases", [{ n: "billing_period", l: "Billing month (YYYY-MM)", v: thisMonth(), req: 1 }],
        async (b) => { const r = await api("/rent/invoices/generate", { method: "POST", body: b }); flash(`Created ${r.created}, skipped ${r.skipped}`); go("rent"); }, "Generate"));
      if (leases.length) box.append(h("div", { class: "row" }, h("button", { class: "sec", onclick: act(async () => { await api("/notifications/run-reminders", { method: "POST" }); flash("Reminders generated"); }) }, "Send due/overdue/expiry reminders")));
    }
    const pay = act(async (r) => {
      const p = await api("/payments/create", { method: "POST", body: { invoice_id: r.id } });
      if (confirm(`Mock checkout for ${inr(p.amount)}.\nOK = simulate success, Cancel = simulate failure`)) {
        const out = await api(`/payments/${p.id}/simulate`, { method: "POST", body: { outcome: "success" } }); flash("Payment " + out.status);
      } else { await api(`/payments/${p.id}/simulate`, { method: "POST", body: { outcome: "failure" } }); flash("Payment failed", true); }
      go("rent");
    });
    box.append(h("div", { class: "card" }, h("h3", {}, "Invoices"), table(rows, [{ t: "Period", f: (r) => r.billing_period }, { t: "Unit", f: (r) => r.unit_number },
      { t: "Tenant", f: (r) => r.tenant_name }, { t: "Amount", f: (r) => inr(r.amount) }, { t: "Paid", f: (r) => inr(r.amount_paid) }, { t: "Due", f: (r) => r.due_date },
      { t: "Status", f: (r) => badge(r.status) },
      { t: "Actions", f: (r) => h("span", {},
        !L && !["paid", "cancelled"].includes(r.status) && h("button", { onclick: () => pay(r) }, "Pay"),
        L && !["paid", "cancelled"].includes(r.status) && h("button", { class: "sec", onclick: act(async () => { await api(`/rent/invoices/${r.id}/mark-paid`, { method: "POST" }); go("rent"); }) }, "Mark paid"),
        L && r.status !== "paid" && r.status !== "cancelled" && !r.amount_paid && h("button", { class: "danger", onclick: act(async () => { await api(`/rent/invoices/${r.id}/cancel`, { method: "POST" }); go("rent"); }) }, "Cancel")) }])),
    h("div", { class: "card" }, h("h3", {}, "Payments"), table(pays, [{ t: "Ref", f: (r) => r.provider_reference }, { t: "Period", f: (r) => r.billing_period }, { t: "Amount", f: (r) => inr(r.amount) },
      { t: "Status", f: (r) => badge(r.status) }, { t: "Paid at", f: (r) => r.paid_at || "-" },
      { t: "", f: (r) => h("span", {}, r.status === "succeeded" && h("button", { class: "sec", onclick: () => download(`/payments/${r.id}/receipt`, `receipt-${r.id}.pdf`) }, "Receipt"),
        L && r.status === "succeeded" && h("button", { class: "danger", onclick: act(async () => { if (confirm("Refund this payment?")) { await api(`/payments/${r.id}/refund`, { method: "POST" }); go("rent"); } }) }, "Refund")) }])));
  },

  async maintenance(box) {
    const rows = await api("/maintenance");
    box.append(h("h2", {}, "Maintenance"), h("div", { class: "msg" }, "If there is immediate danger to life or safety (fire, gas leak, electrical shock), contact emergency services first."));
    if (S.user.role !== "maintenance") {
      const fields = [{ n: "title", l: "Title", req: 1 }, { n: "description", l: "Description", t: "textarea" },
        { n: "category", l: "Category", opts: ["plumbing", "electrical", "cleaning", "appliance", "structural", "other"] },
        { n: "priority", l: "Priority", opts: ["low", "medium", "high", "critical"], v: "medium" }];
      if (S.user.role === "landlord") { const u = await api("/units"); fields.unshift({ n: "unit_id", l: "Unit", opts: u.map((x) => ({ v: x.id, l: `${x.property_name} – ${x.unit_number}` })), req: 1 }); }
      box.append(formCard("New request", fields, async (b) => { if (b.unit_id) b.unit_id = +b.unit_id; await api("/maintenance", { method: "POST", body: b }); flash("Request submitted"); go("maintenance"); }, "Submit"));
    }
    box.append(h("div", { class: "card" }, table(rows, [{ t: "No.", f: (r) => r.number }, { t: "Title", f: (r) => r.title }, { t: "Unit", f: (r) => r.unit_number },
      { t: "Priority", f: (r) => badge(r.priority) }, { t: "Status", f: (r) => badge(r.status) }, { t: "Assigned", f: (r) => r.assigned_name || "-" },
      { t: "", f: (r) => h("button", { class: "sec", onclick: () => go("ticket", r.id) }, "Open") }])));
  },
  async ticket(box, id) {
    const t = await api("/maintenance/" + id);
    const role = S.user.role;
    const reload = () => go("ticket", id);
    box.append(h("button", { class: "sec", onclick: () => go("maintenance") }, "← Back"), h("h2", {}, `${t.number}: ${t.title}`),
      h("div", { class: "card" }, h("div", {}, t.description || "(no description)"), h("div", { class: "muted" }, `${t.property_name} · ${t.unit_number} · ${t.category}`),
        h("div", {}, badge(t.priority), " ", badge(t.status)), t.safety_notice && h("div", { class: "msg" }, t.safety_notice)));
    if (role === "landlord") {
      const staff = await api("/staff");
      box.append(formCard("Assign", [{ n: "assigned_to", l: "Staff", opts: staff.map((s) => ({ v: s.id, l: s.name })), req: 1 }],
        async (b) => { await api("/maintenance/" + id, { method: "PATCH", body: { assigned_to: +b.assigned_to } }); reload(); }, "Assign"));
    }
    const nexts = { landlord: ["in_progress", "waiting_for_tenant", "resolved", "closed", "open"], maintenance: ["in_progress", "waiting_for_tenant", "resolved"], tenant: t.status === "resolved" ? ["closed"] : [] }[role] || [];
    if (nexts.length) box.append(h("div", { class: "card" }, h("h3", {}, "Update status"), nexts.map((s) => h("button", { class: "sec", onclick: act(async () => { await api("/maintenance/" + id, { method: "PATCH", body: { status: s } }); reload(); }) }, s.replace(/_/g, " ")))));
    if (role === "landlord") {
      box.append(formCard("Record cost", [{ n: "description", l: "Description" }, { n: "amount", l: "Amount (₹)", t: "number", req: 1 }, { n: "cost_type", l: "Type", opts: ["labor", "parts", "other"] }],
        async (b) => { await api(`/maintenance/${id}/costs`, { method: "POST", body: b }); reload(); }, "Add cost"),
        h("div", { class: "card" }, h("h3", {}, "Costs · total " + inr(t.total_cost)), table(t.costs, [{ t: "Type", f: (r) => r.cost_type }, { t: "Description", f: (r) => r.description }, { t: "Amount", f: (r) => inr(r.amount) }])));
    }
    const docs = await api("/documents?ticket_id=" + id);
    const fileInput = h("input", { type: "file", accept: ".pdf,.png,.jpg,.jpeg" });
    box.append(h("div", { class: "card" }, h("h3", {}, "Attachments"),
      docs.map((d) => h("div", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); download(`/documents/${d.id}/download`, d.filename); } }, d.filename), ` (${Math.round(d.size / 1024)} KB, ${d.document_type})`)),
      fileInput, h("button", { onclick: act(async () => { if (!fileInput.files[0]) return flash("Choose a file first", true); const f = new FormData(); f.append("file", fileInput.files[0]); f.append("ticket_id", id); f.append("document_type", role === "maintenance" ? "completion_photo" : "attachment"); await api("/documents", { method: "POST", form: f }); reload(); }) }, "Upload")));
    const ci = h("input", { placeholder: "Add a comment" });
    box.append(h("div", { class: "card" }, h("h3", {}, "Comments"), t.comments.map((c) => h("div", {}, h("b", {}, c.author + ": "), c.body, h("span", { class: "muted" }, "  " + c.created_at.slice(0, 16).replace("T", " ")))),
      ci, h("button", { onclick: act(async () => { if (!ci.value.trim()) return; await api(`/maintenance/${id}/comments`, { method: "POST", body: { body: ci.value } }); reload(); }) }, "Post")));
  },

  async screening(box) {
    const rows = await api("/screening");
    box.append(h("h2", {}, "Tenant screening (mock provider)"), h("div", { class: "msg" }, "Demo uses mock data only. Real screening requires tenant consent, a compliant provider and legal review; never decide on an automated result alone."));
    if (S.user.role === "landlord") {
      const tenants = await api("/tenants");
      box.append(formCard("Request screening", [{ n: "tenant_id", l: "Tenant", opts: tenants.map((t) => ({ v: t.id, l: t.name })), req: 1 }],
        async (b) => { await api("/screening", { method: "POST", body: { tenant_id: +b.tenant_id } }); go("screening"); }, "Request"));
    }
    box.append(h("div", { class: "card" }, table(rows, [{ t: "ID", f: (r) => r.id }, { t: "Tenant", f: (r) => r.tenant_name }, { t: "Status", f: (r) => badge(r.status) },
      { t: "Requested", f: (r) => r.requested_at.slice(0, 10) }, { t: "Result ref", f: (r) => r.result_reference || "-" },
      { t: "", f: (r) => S.user.role === "tenant" && r.status === "awaiting_consent" ? h("span", {},
        h("button", { onclick: act(async () => { await api(`/screening/${r.id}/consent`, { method: "POST", body: { consent: true } }); go("screening"); }) }, "Give consent"),
        h("button", { class: "danger", onclick: act(async () => { await api(`/screening/${r.id}/consent`, { method: "POST", body: { consent: false } }); go("screening"); }) }, "Decline")) : "" }])));
  },

  async notifications(box) {
    const rows = await api("/notifications");
    box.append(h("h2", {}, "Notifications"), h("div", { class: "card" }, rows.length ? rows.map((n) => h("div", { style: "padding:6px 0;border-bottom:1px solid var(--line)" },
      h("b", {}, (n.read_at ? "" : "● ") + n.title), h("div", {}, n.message), h("span", { class: "muted" }, n.created_at.slice(0, 16).replace("T", " ") + " "),
      !n.read_at && h("button", { class: "sec", onclick: act(async () => { await api(`/notifications/${n.id}/read`, { method: "POST" }); go("notifications"); }) }, "Mark read"))) : h("span", { class: "muted" }, "No notifications")));
  },

  async users(box) {
    const rows = await api("/admin/users");
    box.append(h("h2", {}, "Users"), h("div", { class: "card" }, table(rows, [{ t: "ID", f: (r) => r.id }, { t: "Name", f: (r) => r.name }, { t: "Email", f: (r) => r.email }, { t: "Role", f: (r) => r.role },
      { t: "Status", f: (r) => badge(r.status) }, { t: "", f: (r) => r.id === S.user.id ? "" : h("button", { class: r.status === "active" ? "danger" : "", onclick: act(async () => { await api("/admin/users/" + r.id, { method: "PATCH", body: { status: r.status === "active" ? "suspended" : "active" } }); go("users"); }) }, r.status === "active" ? "Suspend" : "Activate") }])));
  },
  async audit(box) {
    const rows = await api("/admin/audit-logs?limit=200");
    box.append(h("h2", {}, "Audit log"), h("div", { class: "card" }, table(rows, [{ t: "Time", f: (r) => r.created_at.slice(0, 19).replace("T", " ") }, { t: "Actor", f: (r) => r.actor_id ?? "system" },
      { t: "Action", f: (r) => r.action }, { t: "Resource", f: (r) => `${r.resource_type}#${r.resource_id ?? ""}` }])));
  },
};

const NAV = {
  landlord: [["dashboard", "Dashboard"], ["properties", "Properties"], ["tenants", "Tenants"], ["leases", "Leases"], ["rent", "Rent"], ["maintenance", "Maintenance"], ["screening", "Screening"], ["staff", "Staff"], ["notifications", "Notifications"]],
  tenant: [["dashboard", "Dashboard"], ["leases", "My lease"], ["rent", "Rent"], ["maintenance", "Maintenance"], ["screening", "Screening"], ["notifications", "Notifications"]],
  maintenance: [["dashboard", "Dashboard"], ["maintenance", "My tickets"], ["notifications", "Notifications"]],
  admin: [["dashboard", "Dashboard"], ["users", "Users"], ["audit", "Audit log"]],
};
const PARENT = { property: "properties", ticket: "maintenance" };

function go(view, arg) { S.view = view; S.arg = arg; render(); }

async function render() {
  $app.replaceChildren();
  if (!S.token) return renderAuth();
  if (!S.user) {
    try { S.user = await api("/auth/me"); } catch (_) { return logout(); }
  }
  const nav = NAV[S.user.role] || [];
  if (!S.view || !views[S.view]) S.view = "dashboard";
  const active = PARENT[S.view] || S.view;
  const main = h("div", { class: "main" });
  $app.append(h("div", { class: "shell" },
    h("div", { class: "side" }, h("h1", {}, "🏠 PropManager"), h("div", { class: "muted" }, `${S.user.name} (${S.user.role})`),
      nav.map(([v, l]) => h("a", { class: v === active ? "on" : "", onclick: () => go(v) }, l)),
      h("a", { onclick: logout }, "Log out")), main));
  try { await views[S.view](main, S.arg); } catch (e) { main.append(h("div", { class: "msg err" }, e.message)); }
}

function renderAuth() {
  let mode = "login";
  const box = h("div", { class: "auth" });
  const draw = () => {
    box.replaceChildren();
    const fields = [{ n: "email", l: "Email", t: "email", req: 1 }, { n: "password", l: "Password (min 8 chars)", t: "password", req: 1 }];
    if (mode === "register") fields.unshift({ n: "name", l: "Full name", req: 1 });
    box.append(h("h2", {}, "🏠 Property Management"), h("p", { class: "muted" }, mode === "login" ? "Sign in to continue" : "Create a landlord account (tenants are invited by their landlord)"),
      formCard(mode === "login" ? "Sign in" : "Register", fields, async (b) => {
        const r = await api(mode === "login" ? "/auth/login" : "/auth/register", { method: "POST", body: b });
        S.token = r.token; S.user = r.user; localStorage.setItem("pm_token", r.token); S.view = "dashboard"; render();
      }, mode === "login" ? "Sign in" : "Create account"),
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); mode = mode === "login" ? "register" : "login"; draw(); } }, mode === "login" ? "New landlord? Register" : "Have an account? Sign in"),
      h("div", {}, h("a", { href: "#", onclick: act(async (e) => { e.preventDefault(); const em = prompt("Account email"); if (!em) return; const r = await api("/auth/password-reset/request", { method: "POST", body: { email: em } });
        if (r.dev_token) { const np = prompt("Dev mode: enter a new password (min 8 chars)"); if (np) { await api("/auth/password-reset/confirm", { method: "POST", body: { token: r.dev_token, new_password: np } }); alert("Password updated"); } } else alert(r.message); }) }, "Forgot password?")));
  };
  draw();
  $app.append(box);
}

render();
