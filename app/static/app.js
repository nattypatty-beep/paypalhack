/* MandatePay frontend — vanilla JS, no build step.
   The server is the source of truth; this file renders state, talks to the API,
   and runs the demo director. Money is always handled as integer cents. */

(() => {
  "use strict";

  // ------------------------------------------------------------------ setup
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const DEMO_PROMPT =
    "Get me a logo for my bakery, max $60, ask me before anything over $40.";

  const DEMO_PACKAGES = {
    concept: {
      title: "Concept board",
      notes:
        "Three directions for the bakery identity. 1) A hearth-and-steam mark with a warm serif " +
        "wordmark. 2) An artisan bread loaf built from three simple strokes. 3) A circular seal with " +
        "a wheat motif. My recommendation is direction 2: it reads at small sizes, feels warm and " +
        "hand-made, and gives the bakery a friendly, local voice.",
      samples: ["concept-board.svg"],
    },
    draft: {
      title: "Draft logo",
      notes:
        "Early draft attached for the logo master. Placeholder layout with a quick colour test. " +
        "The mark still needs a proper wordmark and the palette is not final yet.",
      samples: ["logo-draft.svg"],
    },
    final: {
      title: "Final logo",
      notes:
        "Final logo master for the Hearth & Crumb bakery. The mark pairs a warm artisan loaf with a " +
        "hand-drawn steam line and a confident wordmark. It uses a three-colour palette, has a " +
        "transparent background, and is delivered as editable vector source so it scales from a " +
        "shopfront sign to a social avatar without losing clarity.",
      samples: ["logo-final.svg"],
    },
    brandkit: {
      title: "Brand kit",
      notes:
        "Final brand kit and handoff. Included are the vector master and a short usage guide. The " +
        "work is delivered under a perpetual licence with full source files and commercial usage " +
        "rights, so the bakery can use it on packaging, signage and digital channels.",
      samples: ["logo-final.svg", "brand-guide.md"],
    },
  };

  const DEMO_STAGES = [
    { key: "mandate", title: "Draft the mandate & plan", status: "Turning language into policy…" },
    { key: "probe", title: "Probe the budget with $75", status: "Asking the mandate engine for $75…" },
    { key: "job", title: "Post the $60 job", status: "Posting the revised job inside the budget…" },
    { key: "concept", title: "Verify concept directions", status: "Submitting the concept board…" },
    { key: "draft", title: "Catch a weak draft", status: "Submitting a draft that should fail…" },
    { key: "final", title: "Release the logo master", status: "Submitting the final vector…" },
    { key: "brandkit", title: "Release the brand kit", status: "Submitting the handoff package…" },
    { key: "audit", title: "Verify the hash chain", status: "Checking every audit row…" },
  ];

  const PILL = {
    allow: "pill--ok",
    pass: "pill--ok",
    completed: "pill--ok",
    captured: "pill--ok",
    released: "pill--ok",
    authorized: "pill--accent",
    in_progress: "pill--info",
    submitted: "pill--info",
    pending: "pill--info",
    planned: "pill--info",
    awaiting_approval: "pill--warn",
    require_approval: "pill--warn",
    revision: "pill--warn",
    awaiting_review: "pill--warn",
    escalate: "pill--warn",
    blocked: "pill--bad",
    rejected: "pill--bad",
    revise: "pill--bad",
    failed: "pill--bad",
    error: "pill--bad",
    payment_error: "pill--bad",
    cancelled: "pill--muted",
  };

  const REASON_TEXT = {
    budget_exceeded: "budget exceeded",
    category_not_allowed: "category outside the mandate",
    mandate_expired: "mandate expired",
    mandate_inactive: "mandate is not active",
    per_transaction_limit: "over the per-transaction limit",
    daily_limit: "over the daily limit",
    currency_mismatch: "currency mismatch",
    approval_threshold: "above the approval threshold",
    approval_used: "approval already used",
    approval_expired: "approval expired",
    approval_amount_mismatch: "approval is for another amount",
    approval_job_mismatch: "approval is for another job",
    approval_category_mismatch: "approval is for another category",
    authorization_exhausted: "authorized amount exhausted",
    invalid_amount: "invalid amount",
  };

  const state = {
    data: null,
    commandPreview: null,
    stream: null,
    refreshTimer: null,
    pollTimer: null,
    clockTimer: null,
    lastVersion: -1,
    studio: { uploads: [], result: null, ready: false },
    demo: {
      stage: 0,
      running: false,
      context: {},
      pendingApproval: null,
      approvalResolve: null,
    },
    modal: { approval: null, resolving: false },
    grids: {
      audit: null,
      payments: null,
      tab: "audit",
      search: "",
      decision: "",
    },
  };

  class DemoStop extends Error {}

  // --------------------------------------------------------------- helpers
  function money(cents, currency = "USD") {
    if (cents === null || cents === undefined || Number.isNaN(Number(cents))) return "—";
    try {
      return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: String(currency || "USD").toUpperCase(),
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(Number(cents) / 100);
    } catch {
      return `$${(Number(cents) / 100).toFixed(2)}`;
    }
  }

  function compactMoney(cents, currency = "USD") {
    if (cents === null || cents === undefined) return "—";
    const value = Number(cents) / 100;
    if (Math.abs(value) >= 1000) {
      return money(cents, currency).replace(/\.00$/, "");
    }
    return money(cents, currency);
  }

  function toCents(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.round(parsed * 100) : NaN;
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => {
      switch (character) {
        case "&": return "&amp;";
        case "<": return "&lt;";
        case ">": return "&gt;";
        case '"': return "&quot;";
        case "'": return "&#39;";
        default: return character;
      }
    });
  }

  function human(value) {
    return String(value ?? "").replace(/_/g, " ");
  }

  function pillClass(value) {
    return PILL[value] || "pill--info";
  }

  function statusPill(value, label) {
    return `<span class="pill ${pillClass(value)}">${esc(label || human(value) || "unknown")}</span>`;
  }

  function reasonLabel(code) {
    return REASON_TEXT[code] || human(code);
  }

  function decisionReasons(decision) {
    if (!decision) return "No decision returned.";
    const codes = decision.reason_codes || [];
    if (!codes.length) return "Allowed inside the mandate.";
    return codes.map(reasonLabel).join(" · ");
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const timestamp = Date.parse(iso);
    if (Number.isNaN(timestamp)) return "";
    const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
    if (seconds < 8) return "just now";
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return new Date(timestamp).toLocaleDateString();
  }

  function clockTime(iso) {
    const timestamp = Date.parse(iso);
    if (Number.isNaN(timestamp)) return "";
    return new Date(timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function expiryText(iso) {
    if (!iso) return "no expiry";
    const timestamp = Date.parse(iso);
    if (Number.isNaN(timestamp)) return "no expiry";
    const seconds = Math.round((timestamp - Date.now()) / 1000);
    if (seconds <= 0) return "expired";
    if (seconds < 60) return `${seconds}s left`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes}m left`;
    const days = Math.round(minutes / 60 / 24);
    return `${days}d left`;
  }

  function feasibleMilestones() {
    const jobs = (state.data?.jobs || []).filter((job) =>
      ["authorized", "in_progress"].includes(job.status)
    );
    const result = [];
    for (const job of jobs) {
      for (const milestone of job.milestones || []) {
        if (milestone.status !== "released") result.push({ milestone, job });
      }
    }
    return result;
  }

  function findJob(jobId) {
    return (state.data?.jobs || []).find((job) => job.id === jobId) || null;
  }

  function findMilestone(milestoneId) {
    for (const job of state.data?.jobs || []) {
      const milestone = (job.milestones || []).find((item) => item.id === milestoneId);
      if (milestone) return { milestone, job };
    }
    return { milestone: null, job: null };
  }

  function activeMandate() {
    const data = state.data;
    if (!data) return null;
    if (data.active_mandate_id) {
      return (data.mandates || []).find((item) => item.id === data.active_mandate_id) || null;
    }
    return (data.mandates || [])[0] || null;
  }

  // ------------------------------------------------------------------- api
  async function api(path, options = {}) {
    const request = { ...options };
    request.headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body !== undefined && !(options.body instanceof FormData)) {
      request.headers["Content-Type"] = "application/json";
      request.body = typeof options.body === "string" ? options.body : JSON.stringify(options.body);
    }
    const response = await fetch(path, request);
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = text;
      }
    }
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      if (payload && typeof payload === "object") {
        if (payload.error) message = String(payload.error);
        else if (Array.isArray(payload.detail)) {
          message = payload.detail.map((item) => item.msg || item.message || "").join("; ");
        }
      } else if (typeof payload === "string" && payload.trim()) {
        message = payload.trim();
      }
      throw new Error(message);
    }
    return payload;
  }

  // --------------------------------------------------------------- toasts
  function toast(title, message = "", kind = "info", ttl = 5200) {
    const stack = $("#toasts");
    if (!stack) return;
    const node = document.createElement("div");
    node.className = `toast is-${kind}`;
    const icon = kind === "success" ? "✓" : kind === "error" ? "!" : "i";
    node.innerHTML = `
      <span class="toast-icon" aria-hidden="true">${icon}</span>
      <div><strong>${esc(title)}</strong>${message ? `<p>${esc(message)}</p>` : ""}</div>`;
    stack.appendChild(node);
    const remove = () => {
      node.style.opacity = "0";
      node.style.transform = "translateX(10px)";
      setTimeout(() => node.remove(), 220);
    };
    const timer = setTimeout(remove, ttl);
    node.addEventListener("click", () => {
      clearTimeout(timer);
      remove();
    });
  }

  // --------------------------------------------------------- state + stream
  function setConnection(mode, label) {
    const badge = $("#connectionBadge");
    if (!badge) return;
    badge.classList.toggle("is-live", mode === "live");
    badge.classList.toggle("is-polling", mode === "polling");
    badge.classList.toggle("is-offline", mode === "offline");
    const copy = badge.querySelector("span:last-child");
    if (copy) copy.textContent = label;
    badge.title =
      mode === "live"
        ? "Live updates connected"
        : mode === "polling"
          ? "Live stream unavailable — polling for changes"
          : "Server unreachable — retrying";
  }

  async function refreshState({ quiet = false } = {}) {
    try {
      const data = await api("/api/state");
      state.data = data;
      state.lastVersion = data.version;
      renderAll();
      if (!quiet && data.version !== undefined) {
        // no-op: version is tracked for future incremental rendering
      }
      if (!state.stream || state.stream.readyState === EventSource.CLOSED) {
        setConnection("polling", "polling");
      }
      return data;
    } catch (error) {
      setConnection("offline", "offline");
      if (!quiet) toast("Cannot reach the server", error.message, "error");
      throw error;
    }
  }

  function scheduleRefresh() {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => {
      refreshState({ quiet: true }).catch(() => {});
    }, 240);
  }

  function connectStream() {
    if (!("EventSource" in window)) {
      setConnection("polling", "polling");
      return;
    }
    try {
      const stream = new EventSource("/api/stream");
      state.stream = stream;
      stream.onopen = () => setConnection("live", "live");
      stream.onerror = () => setConnection("polling", "polling");
      stream.addEventListener("state", () => scheduleRefresh());
    } catch {
      setConnection("polling", "polling");
    }
  }

  // ---------------------------------------------------------------- engine
  function renderEngineBadges() {
    const host = $("#engineBadges");
    if (!host) return;
    if (!state.data) {
      host.innerHTML = `<span class="engine-badge"><span class="dot"></span>connecting…</span>`;
      return;
    }
    const config = state.data.config || {};
    const mode = String(config.paypal_mode || "mock");
    const paypalLabel =
      mode === "live" ? "PayPal live" : mode === "sandbox" ? "PayPal sandbox" : "PayPal mock";
    const paypalClass = mode === "sandbox" || mode === "live" ? "" : "is-mock";
    const llm = Boolean(config.llm_enabled);
    const plannerLabel = llm ? "AI planner" : "Rules engine";
    const plannerDetail = llm
      ? String(config.llm_model || config.llm_provider || "model").slice(0, 28)
      : "no key · deterministic";
    const payout = config.paypal_payouts ? "payouts on" : "payouts off";
    host.innerHTML = `
      <span class="engine-badge ${paypalClass}" title="PayPal integration mode">
        <span class="dot"></span>${paypalLabel} <b>· ${esc(payout)}</b>
      </span>
      <span class="engine-badge ${llm ? "" : "is-rules"}" title="Planning and verification engine">
        <span class="dot"></span>${plannerLabel} <b>· ${esc(plannerDetail)}</b>
      </span>
      <span class="engine-badge" title="Audit grid">
        <span class="dot"></span>AG Grid <b>· Community</b>
      </span>`;
  }

  // --------------------------------------------------------------- mandate
  function renderMandate() {
    const mandate = activeMandate();
    const body = $("#mandateBody");
    const empty = $("#mandateEmpty");
    const status = $("#mandateStatus");
    if (!mandate) {
      body.hidden = true;
      empty.hidden = false;
      status.textContent = "no mandate";
      status.className = "pill";
      $("#ruleList").innerHTML = "";
      return;
    }

    body.hidden = false;
    empty.hidden = true;
    const financials = mandate.financials || {};
    const budget = Number(mandate.budget_cents || 0);
    const spent = Number(financials.spent_cents || 0);
    const committed = Number(financials.committed_cents || 0);
    const pending = Number(financials.pending_cents || 0);
    const available = Number(financials.available_cents || 0);
    const used = Math.min(budget, spent + committed + pending);
    const percent = budget > 0 ? Math.round((used / budget) * 100) : 0;

    const expired = mandate.expires_at && Date.parse(mandate.expires_at) < Date.now();
    status.textContent = expired ? "expired" : String(mandate.status || "active");
    status.className = `pill ${expired || mandate.status !== "active" ? "pill--bad" : "pill--ok"}`;

    $("#budgetSpent").textContent = money(spent, mandate.currency);
    $("#budgetOf").textContent = `of ${money(budget, mandate.currency)}`;
    $("#budgetCaption").textContent =
      `Released ${money(spent, mandate.currency)} · committed ${money(committed, mandate.currency)}` +
      (pending ? ` · pending ${money(pending, mandate.currency)}` : "");
    $("#budgetExpiry").textContent = mandate.expires_at
      ? `${expired ? "expired" : "expires"} ${new Date(mandate.expires_at).toLocaleDateString()}`
      : "";

    const progress = $("#budgetProgress");
    progress.classList.toggle("is-warm", percent >= 65 && percent < 90);
    progress.classList.toggle("is-hot", percent >= 90);
    progress.setAttribute("aria-valuenow", String(percent));
    $("#budgetBar").style.width = `${percent}%`;

    $("#statCommitted").textContent = money(committed, mandate.currency);
    $("#statPending").textContent = money(pending, mandate.currency);
    $("#statAvailable").textContent = money(available, mandate.currency);
    $("#statReleased").textContent = money(spent, mandate.currency);

    const rules = [];
    rules.push({
      icon: "$",
      title: `Budget ${money(budget, mandate.currency)}`,
      note: "Hard block. An approval cannot override the budget.",
    });
    rules.push({
      icon: "✋",
      title: `Ask above ${money(mandate.approval_threshold_cents, mandate.currency)}`,
      note: "Single-use approval, bound to one job, amount and category.",
    });
    if (mandate.per_txn_limit_cents !== null && mandate.per_txn_limit_cents !== undefined) {
      rules.push({
        icon: "↕",
        title: `Max ${money(mandate.per_txn_limit_cents, mandate.currency)} per transaction`,
        note: "Hard block on every authorization and capture.",
      });
    }
    if (mandate.daily_limit_cents !== null && mandate.daily_limit_cents !== undefined) {
      rules.push({
        icon: "◷",
        title: `Daily cap ${money(mandate.daily_limit_cents, mandate.currency)}`,
        note: "Rolling 24-hour authorization total.",
      });
    }
    const categories = (mandate.categories || []).filter(Boolean);
    rules.push({
      icon: "◇",
      title: categories.length ? `Categories: ${categories.join(", ")}` : "Any category",
      note: "The job category must fit the mandate.",
    });
    rules.push({
      icon: "⌛",
      title: mandate.expires_at
        ? `${expired ? "Expired" : "Expires"} ${new Date(mandate.expires_at).toLocaleDateString()}`
        : "No expiry set",
      note: "Expired mandates block new authorizations automatically.",
    });

    $("#ruleList").innerHTML = rules
      .map(
        (rule) => `<li>
          <span class="rule-icon" aria-hidden="true">${esc(rule.icon)}</span>
          <span><strong>${esc(rule.title)}</strong><em>${esc(rule.note)}</em></span>
        </li>`
      )
      .join("");
  }

  // ----------------------------------------------------------------- story
  function renderStory() {
    const data = state.data;
    const steps = $$("#storySteps li");
    if (!steps.length) return;
    const jobs = data?.jobs || [];
    const approvals = data?.approvals || [];
    const paid = (data?.payments || []).some(
      (payment) => payment.kind === "payout" && payment.status === "completed"
    );
    const authorized = jobs.some((job) =>
      ["authorized", "in_progress", "completed"].includes(job.status)
    );
    const hasJob = jobs.length > 0;
    const hasMandate = Boolean((data?.mandates || []).length);
    const done = {
      mandate: hasMandate,
      plan: hasJob,
      approval: authorized,
      settle: paid,
    };
    const activeKey = !hasMandate
      ? "mandate"
      : !hasJob
        ? "plan"
        : !authorized
          ? "approval"
          : !paid
            ? "settle"
            : "";
    for (const item of steps) {
      const key = item.dataset.story;
      item.classList.toggle("is-done", Boolean(done[key]));
      item.classList.toggle("is-active", key === activeKey);
    }
  }
  // ------------------------------------------------------------------ jobs
  function renderJobs() {
    const list = $("#jobsList");
    const empty = $("#jobsEmpty");
    const count = $("#jobCount");
    if (!list) return;
    const jobs = state.data?.jobs || [];
    count.textContent = `${jobs.length} job${jobs.length === 1 ? "" : "s"}`;
    if (!jobs.length) {
      list.innerHTML = "";
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    list.innerHTML = jobs.map(renderJobCard).join("");
  }

  function renderJobCard(job) {
    const milestones = job.milestones || [];
    const status = String(job.status || "planned");
    const cardClass = ["blocked", "payment_error", "cancelled", "rejected"].includes(status)
      ? "is-blocked"
      : status === "awaiting_approval"
        ? "is-approval"
        : status === "completed"
          ? "is-done"
          : "";
    const released = milestones.filter((item) => item.status === "released");
    const releasedCents = released.reduce((sum, item) => sum + Number(item.amount_cents || 0), 0);
    const percent = Number(job.total_cents)
      ? Math.round((releasedCents / Number(job.total_cents)) * 100)
      : 0;
    const isLive = ["authorized", "in_progress"].includes(status);
    const progressCopy = `${released.length}/${milestones.length} milestones released · ${money(
      releasedCents,
      job.currency
    )} captured`;

    const headerAction = (() => {
      if (status === "planned") {
        return `<button class="btn btn-small" type="button" data-action="authorize-job" data-job="${esc(
          job.id
        )}">Authorize</button>`;
      }
      if (status === "awaiting_approval") {
        return `<button class="btn btn-small" type="button" data-action="review-job-approval" data-job="${esc(
          job.id
        )}">Review approval</button>`;
      }
      if (["authorized", "in_progress", "blocked", "payment_error", "planned"].includes(status)) {
        return `<button class="btn btn-small btn-ghost" type="button" data-action="cancel-job" data-job="${esc(
          job.id
        )}">Cancel</button>`;
      }
      return "";
    })();

    return `<article class="job-card ${cardClass}">
      <div class="job-head">
        <div class="job-title">
          <h3>${esc(job.title)}</h3>
          <div class="job-meta">
            <span>${esc(job.category)}</span><span class="sep">/</span>
            <span>${esc(job.freelancer_name || "freelancer")}</span><span class="sep">/</span>
            <span>${esc(job.id.slice(0, 10))}…</span>
          </div>
        </div>
        <div class="job-right">
          <div class="job-amount">${money(job.total_cents, job.currency)}</div>
          <div class="job-sub">${statusPill(status)}</div>
          ${headerAction ? `<div class="job-actions">${headerAction}</div>` : ""}
        </div>
      </div>
      ${job.blocked_reason ? `<div class="job-blocked">Blocked by the mandate: ${esc(
        String(job.blocked_reason)
          .split(";")
          .map((item) => reasonLabel(item.trim()))
          .join(" · ")
      )}</div>` : ""}
      <div class="job-milestones">
        ${milestones.map((milestone) => renderMilestoneRow(milestone, job, isLive)).join("")}
      </div>
      <div class="job-foot">
        <div class="job-progress" aria-hidden="true"><span style="width:${percent}%"></span></div>
        <small>${esc(progressCopy)}</small>
      </div>
    </article>`;
  }

  function renderMilestoneRow(milestone, job, isLive) {
    const status = String(milestone.status || "pending");
    const criteria = milestone.criteria || [];
    const chips = criteria.slice(0, 3).map((criterion) => {
      const ai = criterion.ai_assisted || criterion.type === "qualitative";
      return `<span class="criterion ${ai ? "is-ai" : ""}" title="${esc(criterion.label)}"><span>${esc(
        criterion.label
      )}</span></span>`;
    });
    if (criteria.length > 3) chips.push(`<span class="criterion"><span>+${criteria.length - 3}</span></span>`);

    const submission = milestone.latest_submission;
    const actions = [];
    if (isLive && status !== "released") {
      actions.push(
        `<button class="btn btn-small" type="button" data-action="use-milestone" data-ms="${esc(
          milestone.id
        )}">Studio</button>`
      );
    }
    if (submission && status !== "released") {
      actions.push(
        `<button class="btn btn-small btn-ghost" type="button" data-action="verify-submission" data-sub="${esc(
          submission.id
        )}">Verify</button>`
      );
    }
    if (status === "awaiting_review" && isLive) {
      actions.push(
        `<button class="btn btn-small btn-danger-ghost" type="button" data-action="release-milestone" data-ms="${esc(
          milestone.id
        )}">Release</button>`
      );
    }
    if (status === "released") {
      actions.push(`<span class="ms-gate">captured</span>`);
    }

    return `<div class="milestone ${state.studio.milestoneId === milestone.id ? "is-selected" : ""}" data-ms-row="${esc(
      milestone.id
    )}">
      <span class="milestone-num">${Number(milestone.idx) + 1}</span>
      <div class="milestone-main">
        <h4>${esc(milestone.title)} ${statusPill(status)}</h4>
        <div class="ms-desc">${esc(milestone.description || "")}</div>
        <div class="criteria-list">${chips.join("")}</div>
      </div>
      <div class="milestone-side">
        <span class="ms-amount">${money(milestone.amount_cents, milestone.currency || job.currency)}</span>
        <div class="ms-actions">${actions.join("")}</div>
      </div>
    </div>`;
  }

  // -------------------------------------------------------------- approvals
  function renderApprovals() {
    const list = $("#approvalList");
    const empty = $("#approvalsEmpty");
    const badge = $("#approvalCount");
    if (!list) return;
    const pending = (state.data?.approvals || []).filter((item) => item.status === "pending");
    badge.textContent = `${pending.length} pending`;
    if (!pending.length) {
      list.innerHTML = "";
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    list.innerHTML = pending
      .map((approval) => {
        const job = approval.job_id ? findJob(approval.job_id) : null;
        const reason = (approval.reasons || [])[0];
        return `<article class="approval-card">
          <div class="approval-top">
            <div>
              <h4>${esc(job?.title || "Spend approval")}</h4>
              <p>${esc(approval.category || "any category")} · ${esc(
                job?.freelancer_name || "freelancer"
              )}</p>
            </div>
            <span class="approval-amount">${money(approval.amount_cents, approval.currency)}</span>
          </div>
          <p>${esc(reason?.message || "This spend is above your human-approval threshold.")}</p>
          <span class="approval-reason">${esc(reason?.code || "approval_threshold")}</span>
          <div class="approval-foot">
            <span class="timer" data-expires="${esc(approval.expires_at)}">expires in <strong>${esc(
              expiryText(approval.expires_at)
            )}</strong></span>
            <div class="approval-actions">
              <button class="btn btn-small btn-danger-ghost" type="button" data-action="reject-approval" data-approval="${esc(
                approval.id
              )}">Reject</button>
              <button class="btn btn-small btn-primary" type="button" data-action="open-approval" data-approval="${esc(
                approval.id
              )}">Approve…</button>
            </div>
          </div>
        </article>`;
      })
      .join("");
  }

  // ------------------------------------------------------------------ feed
  function renderFeed() {
    const host = $("#agentFeed");
    if (!host) return;
    const entries = (state.data?.audit || []).slice(0, 14);
    if (!entries.length) {
      host.innerHTML = `<li class="feed-empty">Events will appear here as the agent works.</li>`;
      return;
    }
    host.innerHTML = entries
      .map((entry) => {
        const decision = String(entry.decision || "info");
        const tone = ["allow", "pass", "captured", "released", "completed", "authorized"].includes(decision)
          ? "good"
          : ["block", "revise", "failed", "error", "rejected"].includes(decision)
            ? "bad"
            : ["require_approval", "awaiting_approval", "revision"].includes(decision)
              ? "warn"
              : "info";
        const symbol = tone === "good" ? "✓" : tone === "bad" ? "!" : tone === "warn" ? "…" : "i";
        const codes = (entry.reason_codes || []).join(", ");
        return `<li>
          <span class="feed-dot is-${tone}" aria-hidden="true">${symbol}</span>
          <div class="feed-body">
            <p>${esc(entry.message || entry.action)}</p>
            <div class="feed-meta">
              <span class="feed-actor">${esc(entry.actor || "system")}</span>
              <span>${esc(entry.action || "")}</span>
              ${codes ? `<span title="${esc(codes)}">· ${esc(codes.slice(0, 42))}</span>` : ""}
            </div>
          </div>
          <time class="feed-time" datetime="${esc(entry.ts)}" title="${esc(clockTime(entry.ts))} · #${esc(
            entry.seq
          )}">${esc(timeAgo(entry.ts))}</time>
        </li>`;
      })
      .join("");
  }

  // ---------------------------------------------------------------- metrics
  function renderAuditStats() {
    const stats = state.data?.stats || {};
    $("#auditReleased").textContent = money(stats.released_cents || 0);
    $("#auditPayouts").textContent = money(stats.payouts_cents || 0);
    $("#auditBlocked").textContent = String(stats.blocked_attempts || 0);
    $("#auditEntries").textContent = String(stats.audit_entries || 0);
    const chain = $("#chainStatus");
    if (!state.data) {
      chain.textContent = "checking…";
      chain.className = "pill";
    } else if (stats.chain_ok) {
      chain.textContent = "chain intact";
      chain.className = "pill pill--ok";
      chain.title = `${stats.audit_entries || 0} rows verified with SHA-256`;
    } else {
      chain.textContent = `broken at #${stats.broken_at ?? "?"}`;
      chain.className = "pill pill--bad";
      chain.title = "The audit hash chain did not verify.";
    }
  }

  // -------------------------------------------------------------- AG Grid
  function gridTheme() {
    if (typeof agGrid === "undefined") return null;
    try {
      const quartz = agGrid.themeQuartz;
      if (quartz && typeof quartz.withPart === "function" && agGrid.colorSchemeDark) {
        try {
          return quartz.withPart(agGrid.colorSchemeDark).withParams({ accentColor: "#7af0bd" });
        } catch {
          return quartz.withPart(agGrid.colorSchemeDark);
        }
      }
      if (quartz && typeof quartz.withParams === "function") {
        return quartz.withParams({ accentColor: "#7af0bd" });
      }
    } catch (error) {
      console.warn("AG Grid Theming API unavailable; using the CSS fallback.", error);
    }
    return null;
  }

  function decisionCell(params) {
    const span = document.createElement("span");
    const value = String(params.value || "info");
    span.className = `cell-decision ${value}`;
    span.textContent = human(value);
    return span;
  }

  function auditRowData() {
    const decision = state.grids.decision;
    const rows = state.data?.audit || [];
    return decision ? rows.filter((row) => row.decision === decision) : rows;
  }

  function paymentRowData() {
    return state.data?.payments || [];
  }

  function baseGridOptions(rowData, columnDefs, name) {
    const options = {
      rowData,
      columnDefs,
      defaultColDef: { sortable: true, resizable: true, suppressHeaderMenuButton: true },
      rowHeight: 35,
      headerHeight: 35,
      animateRows: false,
      suppressCellFocus: true,
      tooltipShowDelay: 250,
      overlayNoRowsTemplate: `<span class="cell-muted">${esc(name)} is empty.</span>`,
    };
    const theme = gridTheme();
    if (theme) options.theme = theme;
    return options;
  }

  function createGridWithFallback(host, options, kind) {
    // The vendored AG Grid ships its Theming API only. If the theme is missing we
    // render the plain HTML fallback instead of an unstyled grid.
    if (typeof agGrid === "undefined" || typeof agGrid.createGrid !== "function" || !options.theme) {
      renderFallbackGrid(host, kind);
      return null;
    }
    try {
      return agGrid.createGrid(host, options);
    } catch (error) {
      console.warn(`AG Grid ${kind} unavailable; using the HTML fallback.`, error);
      host.innerHTML = "";
      renderFallbackGrid(host, kind);
      return null;
    }
  }

  function ensureAuditGrid() {
    const host = $("#auditGrid");
    if (!host) return;
    if (state.grids.audit) {
      state.grids.audit.setGridOption("rowData", auditRowData());
      return;
    }
    if (!state.data) return;
    const columnDefs = [
      { field: "seq", headerName: "#", width: 60, maxWidth: 72 },
      { field: "ts", headerName: "Time", width: 96, valueFormatter: (params) => clockTime(params.value) },
      { field: "actor", headerName: "Actor", width: 96, cellClass: "cell-actor" },
      { field: "action", headerName: "Action", width: 172, cellClass: "cell-mono" },
      { field: "decision", headerName: "Decision", width: 126, cellRenderer: decisionCell },
      {
        field: "amount_cents",
        headerName: "Amount",
        width: 105,
        type: "numericColumn",
        cellClass: "cell-amount",
        valueFormatter: (params) => (params.value == null ? "" : money(params.value, params.data?.currency || "USD")),
      },
      {
        field: "message",
        headerName: "What happened",
        flex: 1,
        minWidth: 240,
        tooltipField: "message",
        cellClass: "cell-muted",
      },
      {
        field: "hash",
        headerName: "Hash",
        width: 116,
        cellClass: "cell-mono",
        valueFormatter: (params) => (params.value ? `${String(params.value).slice(0, 10)}…` : ""),
        tooltipField: "hash",
      },
    ];
    state.grids.audit = createGridWithFallback(
      host,
      baseGridOptions(auditRowData(), columnDefs, "Audit log"),
      "audit"
    );
    if (state.grids.audit) state.grids.audit.setGridOption("quickFilterText", state.grids.search);
  }

  function ensurePaymentsGrid() {
    const host = $("#paymentsGrid");
    if (!host || !state.data) return;
    if (state.grids.payments) {
      state.grids.payments.setGridOption("rowData", paymentRowData());
      return;
    }
    const columnDefs = [
      {
        field: "created_at",
        headerName: "Time",
        width: 100,
        valueFormatter: (params) => clockTime(params.value),
      },
      { field: "kind", headerName: "Kind", width: 112, cellClass: "cell-mono" },
      { field: "status", headerName: "Status", width: 116, cellRenderer: decisionCell },
      {
        field: "amount_cents",
        headerName: "Amount",
        width: 110,
        type: "numericColumn",
        cellClass: "cell-amount",
        valueFormatter: (params) => money(params.value, params.data?.currency || "USD"),
      },
      {
        field: "job_id",
        headerName: "Job",
        width: 122,
        cellClass: "cell-mono",
        valueFormatter: (params) => (params.value ? `${String(params.value).slice(0, 12)}…` : ""),
      },
      {
        field: "paypal_id",
        headerName: "PayPal id",
        width: 150,
        cellClass: "cell-mono",
        tooltipField: "paypal_id",
        valueFormatter: (params) => (params.value ? String(params.value) : "—"),
      },
      {
        field: "description",
        headerName: "Description",
        flex: 1,
        minWidth: 230,
        cellClass: "cell-muted",
        tooltipField: "description",
      },
    ];
    state.grids.payments = createGridWithFallback(
      host,
      baseGridOptions(paymentRowData(), columnDefs, "Payments"),
      "payments"
    );
    if (state.grids.payments) state.grids.payments.setGridOption("quickFilterText", state.grids.search);
  }

  function renderFallbackGrid(host, kind) {
    const rows = kind === "audit" ? auditRowData() : paymentRowData();
    if (!rows.length) {
      host.innerHTML = `<div class="grid-empty">${kind === "audit" ? "Audit log" : "Payments"} is empty.</div>`;
      return;
    }
    if (kind === "audit") {
      host.innerHTML = `<table class="fallback-grid"><thead><tr><th>#</th><th>Time</th><th>Actor</th><th>Action</th><th>Decision</th><th>Amount</th><th>What happened</th></tr></thead><tbody>${rows
        .slice(0, 80)
        .map(
          (row) => `<tr>
            <td>${esc(row.seq)}</td><td>${esc(clockTime(row.ts))}</td><td>${esc(row.actor)}</td>
            <td>${esc(row.action)}</td><td><span class="cell-decision ${esc(row.decision)}">${esc(
              human(row.decision)
            )}</span></td>
            <td>${row.amount_cents == null ? "" : esc(money(row.amount_cents, row.currency))}</td>
            <td>${esc(row.message)}</td></tr>`
        )
        .join("")}</tbody></table>`;
    } else {
      host.innerHTML = `<table class="fallback-grid"><thead><tr><th>Time</th><th>Kind</th><th>Status</th><th>Amount</th><th>Description</th></tr></thead><tbody>${rows
        .slice(0, 80)
        .map(
          (row) => `<tr><td>${esc(clockTime(row.created_at))}</td><td>${esc(row.kind)}</td>
            <td><span class="cell-decision ${esc(row.status)}">${esc(human(row.status))}</span></td>
            <td>${esc(money(row.amount_cents, row.currency))}</td><td>${esc(row.description)}</td></tr>`
        )
        .join("")}</tbody></table>`;
    }
  }

  function refreshGrids() {
    ensureAuditGrid();
    if (state.grids.audit) {
      state.grids.audit.setGridOption("rowData", auditRowData());
      state.grids.audit.setGridOption("quickFilterText", state.grids.search);
    } else {
      renderFallbackGrid($("#auditGrid"), "audit");
    }
    if (!state.grids.payments && !(state.grids.tab === "payments" && state.grids.payments)) {
      // create lazily on tab switch
    }
    if (state.grids.payments) {
      state.grids.payments.setGridOption("rowData", paymentRowData());
      state.grids.payments.setGridOption("quickFilterText", state.grids.search);
    } else if (state.grids.tab === "payments") {
      renderFallbackGrid($("#paymentsGrid"), "payments");
    }
    $("#gridFoot").textContent =
      state.grids.tab === "audit"
        ? `Showing ${auditRowData().length} of ${(state.data?.audit || []).length} audit rows, newest first.`
        : `Showing ${paymentRowData().length} payment row${paymentRowData().length === 1 ? "" : "s"}, newest first.`;
  }

  // --------------------------------------------------------------- render all
  function renderAll() {
    if (!state.data) return;
    renderEngineBadges();
    renderMandate();
    renderJobs();
    renderApprovals();
    renderFeed();
    renderAuditStats();
    renderStory();
    renderStudioOptions();
    refreshGrids();
    renderTimers();
    restoreLatestVerification();
  }
  // ---------------------------------------------------------------- studio
  function renderStudioOptions() {
    const select = $("#studioMilestone");
    if (!select) return;
    const items = feasibleMilestones();
    const previous = state.studio.milestoneId || select.value;
    select.innerHTML = items.length
      ? items
          .map(
            ({ milestone, job }) =>
              `<option value="${esc(milestone.id)}">${esc(job.title)} · ${esc(
                milestone.title
              )} — ${esc(money(milestone.amount_cents, milestone.currency || job.currency))}</option>`
          )
          .join("")
      : `<option value="">No authorized milestone yet</option>`;
    if (previous && items.some(({ milestone }) => milestone.id === previous)) {
      select.value = previous;
      state.studio.milestoneId = previous;
    } else if (items.length) {
      select.value = items[0].milestone.id;
      state.studio.milestoneId = items[0].milestone.id;
    } else {
      state.studio.milestoneId = null;
    }
    const hint = $("#studioHint");
    if (hint) {
      hint.textContent = items.length
        ? "Pick a milestone, attach files, then submit for verification."
        : "Authorize a job to unlock the verification studio.";
    }
  }

  function applyPackage(quality) {
    const pack = DEMO_PACKAGES[quality];
    if (!pack) return;
    $("#studioQuality").value = quality;
    $("#studioNotes").value = pack.notes;
    for (const input of $$(".check-pill input")) {
      input.checked = pack.samples.includes(input.dataset.sample);
    }
  }

  function renderUploads() {
    const host = $("#uploadList");
    if (!host) return;
    host.innerHTML = state.studio.uploads
      .map(
        (upload) => `<span class="upload-item">${esc(upload.name)}
          <button type="button" data-action="remove-upload" data-upload="${esc(
            upload.id
          )}" aria-label="Remove ${esc(upload.name)}">×</button></span>`
      )
      .join("");
  }

  function renderVerificationEmpty() {
    const host = $("#verificationResult");
    if (!host) return;
    host.innerHTML = `<div class="verify-empty">
      <div class="scope" aria-hidden="true"></div>
      <p>Verification evidence shows up here: each criterion, what was measured, and the verdict.</p>
    </div>`;
    const pill = $("#verifyVerdict");
    pill.textContent = "waiting";
    pill.className = "pill";
  }

  function renderVerification(payload) {
    if (!payload) return;
    const result = payload.verification || payload;
    const submission = payload.submission || null;
    const release = payload.release || null;
    const verdict = String(result.verdict || "revise");
    const host = $("#verificationResult");
    const pill = $("#verifyVerdict");
    pill.textContent = verdict;
    pill.className = `pill ${pillClass(verdict)}`;

    const criteria = result.criteria || [];
    const files = submission?.deliverables || [];
    const engine =
      result.engine === "rules+llm"
        ? `deterministic rules + ${result.model || "model"}`
        : "deterministic rules · no model";
    let releaseCopy = "";
    if (release?.status === "released") {
      const captured = release.capture?.amount_cents;
      const payout = release.payout || {};
      releaseCopy = `Funds released: ${money(captured, release.capture?.currency)} captured` +
        (payout.status === "completed"
          ? `, ${money(payout.net_cents, release.capture?.currency)} paid out to the freelancer.`
          : payout.status === "failed"
            ? ", payout pending (capture succeeded)."
            : ".");
    } else if (release?.status === "blocked") {
      releaseCopy = `Capture blocked: ${decisionReasons(release.decision)}`;
    } else if (verdict === "pass") {
      releaseCopy = "All checks passed. The release was recorded in the audit trail.";
    }

    host.innerHTML = `<article class="verdict-card">
      <div class="verdict-head">
        <div>
          <div class="verdict-title">
            <h4>${verdict === "pass" ? "Accepted" : verdict === "revise" ? "Needs revision" : "Needs a human"}</h4>
            ${statusPill(verdict)}
          </div>
          <p>${esc(engine)} · checked ${esc(timeAgo(result.checked_at))}</p>
        </div>
        <div class="verdict-score">
          <strong>${Math.round(Number(result.score || 0) * 100)}%</strong>
          <span>weighted pass</span>
        </div>
      </div>
      <div class="verdict-summary">
        ${esc(result.summary || "No summary returned.")}
        ${result.ai_notes ? `<span class="ai-note">${esc(result.ai_notes)}</span>` : ""}
        ${releaseCopy ? `<span class="ai-note">${esc(releaseCopy)}</span>` : ""}
      </div>
      <ul class="criteria-results">
        ${criteria
          .map((criterion) => {
            const skipped = Boolean(criterion.skipped);
            const tone = skipped ? "skip" : criterion.passed ? "ok" : "bad";
            const symbol = skipped ? "–" : criterion.passed ? "✓" : "!";
            const source =
              criterion.source === "ai" ? "AI" : criterion.source === "human" ? "human" : "rule";
            return `<li>
              <span class="crit-icon ${tone}" aria-hidden="true">${symbol}</span>
              <div class="crit-body">
                <strong>${esc(criterion.label)}</strong>
                <span>${esc(criterion.evidence || (skipped ? "Not evaluated." : ""))}</span>
              </div>
              <span class="crit-source">${esc(source)}</span>
            </li>`;
          })
          .join("")}
      </ul>
      ${files.length ? `<div class="verdict-files">${files
        .map(
          (file) =>
            `<a class="file-chip" href="${esc(file.url)}" target="_blank" rel="noreferrer">${esc(
              file.name
            )}</a>`
        )
        .join("")}</div>` : ""}
    </article>`;
  }

  function restoreLatestVerification() {
    if (state.studio.result) return;
    let best = null;
    for (const job of state.data?.jobs || []) {
      for (const milestone of job.milestones || []) {
        const submission = milestone.latest_submission;
        if (submission?.verdict) {
          if (!best || String(submission.created_at) > String(best.submission.created_at)) {
            best = { submission, verification: submission.verdict, release: null };
          }
        }
      }
    }
    if (best) {
      state.studio.result = best;
      renderVerification(best);
    }
  }

  async function submitStudio(verify) {
    const milestoneId = $("#studioMilestone")?.value || state.studio.milestoneId;
    if (!milestoneId) {
      toast("No milestone selected", "Authorize a job first, then choose a milestone.", "error");
      return;
    }
    const notes = ($("#studioNotes")?.value || "").trim();
    const samples = $$(".check-pill input:checked").map((input) => input.dataset.sample);
    const uploadIds = state.studio.uploads.map((upload) => upload.id);
    if (!samples.length && !uploadIds.length) {
      toast("Attach at least one file", "Pick a demo package or upload a deliverable.", "error");
      return;
    }
    const buttons = [$("#btnSubmitWork"), $("#btnSubmitVerify")].filter(Boolean);
    buttons.forEach((button) => (button.disabled = true));
    const active = verify ? $("#btnSubmitVerify") : $("#btnSubmitWork");
    setBusy(active, true);
    try {
      const submission = await api(`/api/milestones/${milestoneId}/submissions`, {
        method: "POST",
        body: { notes, samples, upload_ids: uploadIds },
      });
      if (verify) {
        const result = await api(`/api/submissions/${submission.id}/verify`, { method: "POST" });
        state.studio.result = result;
        renderVerification(result);
        const verdict = result.verification?.verdict;
        toast(
          verdict === "pass" ? "Work accepted" : verdict === "revise" ? "Revision requested" : "Human review needed",
          result.verification?.summary || "",
          verdict === "pass" ? "success" : verdict === "revise" ? "error" : "info"
        );
        scrollToPanel("#verifyPanel");
      } else {
        toast("Submission received", "Attach it to the audit trail, then verify when ready.", "success");
      }
      state.studio.uploads = [];
      renderUploads();
      await refreshState({ quiet: true });
    } catch (error) {
      toast("Submission failed", error.message, "error");
    } finally {
      setBusy(active, false);
      buttons.forEach((button) => (button.disabled = false));
    }
  }

  async function verifySubmissionId(submissionId) {
    try {
      const result = await api(`/api/submissions/${submissionId}/verify`, { method: "POST" });
      state.studio.result = result;
      renderVerification(result);
      const verdict = result.verification?.verdict;
      toast(
        `Verifier: ${verdict}`,
        result.verification?.summary || "",
        verdict === "pass" ? "success" : verdict === "revise" ? "error" : "info"
      );
      await refreshState({ quiet: true });
      scrollToPanel("#verifyPanel");
    } catch (error) {
      toast("Verification failed", error.message, "error");
    }
  }

  async function releaseMilestoneAction(milestoneId) {
    try {
      const result = await api(`/api/milestones/${milestoneId}/release`, { method: "POST" });
      if (result.status === "released") {
        toast("Released", "The milestone was captured and the payout was queued.", "success");
      } else if (result.status === "blocked") {
        toast("Release blocked", decisionReasons(result.decision), "error");
      } else {
        toast("Release recorded", human(result.status || "done"), "info");
      }
      await refreshState({ quiet: true });
    } catch (error) {
      toast("Release failed", error.message, "error");
    }
  }

  function selectMilestone(milestoneId) {
    const { milestone } = findMilestone(milestoneId);
    if (!milestone) return;
    state.studio.milestoneId = milestoneId;
    const select = $("#studioMilestone");
    if (select) select.value = milestoneId;
    const quality = Number(milestone.idx) === 0 ? "concept" : Number(milestone.idx) === 1 ? "final" : "brandkit";
    applyPackage(quality);
    renderJobs();
    scrollToPanel("#verifyPanel");
    $("#studioNotes")?.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------ command flow
  function renderCommandResult(payload, { error = "" } = {}) {
    const host = $("#commandResult");
    if (!host) return;
    host.hidden = false;
    if (error) {
      host.className = "command-result is-error";
      host.innerHTML = `<h4>Could not create the mandate</h4><p>${esc(error)}</p>`;
      return;
    }
    host.className = "command-result";
    state.commandPreview = payload;
    const mandate = payload.mandate || {};
    const plan = payload.plan || { milestones: [] };
    const parsed = payload.parsed || {};
    const total = (plan.milestones || []).reduce((sum, item) => sum + Number(item.amount_cents || 0), 0);
    const job = payload.job;
    const categories = (mandate.categories || []).join(", ") || "any";
    const actions = job
      ? `<button class="btn btn-small" type="button" data-action="authorize-job" data-job="${esc(
          job.id
        )}">Authorize job</button>`
      : `<button class="btn btn-small btn-primary" type="button" data-action="post-plan">Post this job</button>`;

    host.innerHTML = `
      <h4>${job ? "Job posted" : "Plan ready"} · ${money(mandate.budget_cents, mandate.currency)} budget</h4>
      <p>${esc(parsed.job_title || plan.title || "Freelance job")} · ${esc(
        parsed.category || "other"
      )} · ask above ${esc(
        money(mandate.approval_threshold_cents, mandate.currency)
      )} · ${esc(categories)}. Parsed by ${esc(parsed.source || "rules")}.</p>
      <ul class="plan-preview">
        ${(plan.milestones || [])
          .map(
            (milestone) =>
              `<li><span>${esc(milestone.title)} · ${(milestone.criteria || []).length} tests</span><strong>${money(
                milestone.amount_cents,
                mandate.currency
              )}</strong></li>`
          )
          .join("")}
        <li><span>Total</span><strong>${money(total, mandate.currency)}</strong></li>
      </ul>
      <div class="result-actions">
        ${actions}
        <button class="btn btn-small btn-ghost" type="button" data-action="open-mandate">View mandate</button>
      </div>`;
  }

  async function postPreviewPlan(button) {
    const preview = state.commandPreview;
    if (!preview?.plan || !preview?.mandate) {
      toast("No plan to post", "Create a mandate and plan first.", "error");
      return;
    }
    const plan = preview.plan;
    const total = (plan.milestones || []).reduce((sum, item) => sum + Number(item.amount_cents || 0), 0);
    setBusy(button, true);
    try {
      const result = await api("/api/jobs", {
        method: "POST",
        body: {
          mandate_id: preview.mandate.id,
          title: plan.title,
          brief: preview.parsed?.job_brief || "",
          category: preview.parsed?.category || "other",
          amount_cents: total,
          currency: preview.mandate.currency,
          plan,
        },
      });
      preview.job = result.job;
      renderCommandResult(preview);
      await refreshState({ quiet: true });
      toast("Job posted", "The plan is live. Authorize escrow when you are ready.", "success");
    } catch (error) {
      toast("Could not post the job", error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  async function submitCommand(event) {
    event.preventDefault();
    const text = $("#commandText").value.trim();
    const post = $("#commandPost").checked;
    if (text.length < 3) {
      toast("Give the agent an instruction", "Try: Get me a logo, max $60, ask me over $40.", "error");
      return;
    }
    const button = $("#btnCommand");
    setBusy(button, true);
    try {
      const result = await api("/api/command", {
        method: "POST",
        body: { text, principal: "you", post },
      });
      renderCommandResult(result);
      await refreshState({ quiet: true });
      toast(
        "Mandate created",
        `${money(result.mandate.budget_cents, result.mandate.currency)} budget · approval above ${money(
          result.mandate.approval_threshold_cents,
          result.mandate.currency
        )}.`,
        "success"
      );
      if (result.job) {
        toast("Agent posted the job", `${result.plan.milestones.length} milestones with acceptance tests.`, "info");
      }
    } catch (error) {
      renderCommandResult(null, { error: error.message });
      toast("Could not create the mandate", error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  // ---------------------------------------------------------- spend simulator
  async function checkSpend(event) {
    event.preventDefault();
    const mandate = activeMandate();
    if (!mandate) {
      toast("No mandate yet", "Create a mandate before testing a spend.", "error");
      return;
    }
    const amount = toCents($("#spendAmount").value);
    if (!Number.isFinite(amount) || amount <= 0) {
      toast("Enter an amount", "Use a positive dollar amount, for example 75.", "error");
      return;
    }
    const category = $("#spendCategory").value;
    const button = $("#btnSpend");
    setBusy(button, true);
    try {
      const decision = await api("/api/spend/check", {
        method: "POST",
        body: {
          mandate_id: mandate.id,
          amount_cents: amount,
          category,
          description: `Spend simulator: ${money(amount, mandate.currency)} on ${category}`,
          record: true,
        },
      });
      renderSpendResult(decision, amount, mandate.currency);
      await refreshState({ quiet: true });
      if (decision.verdict === "block") {
        toast("Blocked by a hard rule", decisionReasons(decision), "error");
      } else if (decision.verdict === "require_approval") {
        toast("A human must approve", "The spend is inside the budget but above your threshold.", "info");
      }
    } catch (error) {
      toast("Spend check failed", error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  function renderSpendResult(decision, amount, currency) {
    const host = $("#spendResult");
    const verdict = String(decision.verdict || "block");
    const reasons = decision.reasons || [];
    host.innerHTML = `
      ${statusPill(verdict)} <strong>${money(amount, currency)}</strong>
      <span style="color:var(--muted)">— ${esc(decisionReasons(decision))}</span>
      ${
        reasons.length
          ? `<div class="sim-reasons">${reasons
              .map((reason) => `<span class="sim-code" title="${esc(reason.message)}">${esc(reason.code)}</span>`)
              .join("")}</div>`
          : ""
      }`;
  }

  // ---------------------------------------------------------- approval modal
  function openApprovalModal(approval) {
    if (!approval) return;
    state.modal.approval = approval;
    const job = approval.job_id ? findJob(approval.job_id) : null;
    const reason = (approval.reasons || [])[0];
    $("#modalAmount").textContent = money(approval.amount_cents, approval.currency);
    $("#modalJob").textContent = job?.title || "Spend approval";
    $("#modalReason").textContent = reason?.code || "approval_threshold";
    $("#modalNote").value = "";
    $("#modalFoot").textContent =
      `Single-use approval · expires in ${expiryText(approval.expires_at)}. ` +
      "Authorizing holds the money; it does not release it.";
    $("#approvalModal").hidden = false;
    setTimeout(() => $("#btnApprove")?.focus(), 40);
  }

  function closeApprovalModal(decision = "dismissed") {
    const modal = $("#approvalModal");
    if (modal) modal.hidden = true;
    state.modal.approval = null;
    if (state.demo.approvalResolve) {
      const resolve = state.demo.approvalResolve;
      state.demo.approvalResolve = null;
      resolve(decision);
    }
  }

  async function decideApproval(approve) {
    const approval = state.modal.approval;
    if (!approval || state.modal.resolving) return;
    state.modal.resolving = true;
    const note = ($("#modalNote").value || "").trim();
    const button = approve ? $("#btnApprove") : $("#btnReject");
    setBusy(button, true);
    try {
      await api(`/api/approvals/${approval.id}/resolve`, {
        method: "POST",
        body: { approve, note },
      });
      if (!approve) {
        toast("Spend rejected", "The agent cannot authorize this job.", "info");
        closeApprovalModal("rejected");
        await refreshState({ quiet: true });
        return;
      }
      let authorized = null;
      if (approval.job_id) {
        authorized = await authorizeJobRequest(approval.job_id, approval.id, { quiet: true });
      }
      if (authorized?.status === "blocked") {
        toast("Approval recorded — but the spend is still blocked", decisionReasons(authorized.decision), "error");
      } else if (authorized?.status === "payment_error") {
        toast("Approved, but PayPal failed", authorized.error || "Check the PayPal sandbox credentials.", "error");
      } else {
        toast(
          "Approved & authorized",
          "PayPal is holding the funds. Nothing is released until the verifier accepts the work.",
          "success"
        );
      }
      closeApprovalModal("approved");
      await refreshState({ quiet: true });
    } catch (error) {
      toast("Approval failed", error.message, "error");
    } finally {
      state.modal.resolving = false;
      setBusy(button, false);
    }
  }

  async function quickReject(approvalId) {
    try {
      await api(`/api/approvals/${approvalId}/resolve`, {
        method: "POST",
        body: { approve: false, note: "Rejected from the approval inbox." },
      });
      toast("Spend rejected", "The agent cannot authorize this job.", "info");
      await refreshState({ quiet: true });
    } catch (error) {
      toast("Could not reject", error.message, "error");
    }
  }

  // ------------------------------------------------------------- job actions
  async function authorizeJobRequest(jobId, approvalId = null, { quiet = false } = {}) {
    try {
      const result = await api(`/api/jobs/${jobId}/authorize`, {
        method: "POST",
        body: approvalId ? { approval_id: approvalId } : {},
      });
      if (result.status === "approval_required") {
        if (!quiet && result.approval) openApprovalModal(result.approval);
        if (!quiet) {
          toast(
            "A human must approve",
            `${money(result.approval?.amount_cents, result.approval?.currency)} is above your threshold.`,
            "info"
          );
        }
      } else if (result.status === "blocked") {
        toast("Authorization blocked", decisionReasons(result.decision), "error");
      } else if (result.status === "payment_error") {
        toast("PayPal could not authorize", result.error || "Check the sandbox credentials.", "error");
      } else if (result.status === "authorized" && !quiet) {
        toast(
          "Authorized",
          "Funds are held, not released. Each milestone is captured after verification.",
          "success"
        );
      }
      if (!quiet) await refreshState({ quiet: true });
      return result;
    } catch (error) {
      toast("Authorization failed", error.message, "error");
      if (!quiet) await refreshState({ quiet: true }).catch(() => {});
      return { status: "error", error: error.message };
    }
  }

  async function cancelJobAction(jobId) {
    try {
      await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
      toast("Job cancelled", "Any remaining authorization was voided.", "info");
      await refreshState({ quiet: true });
    } catch (error) {
      toast("Could not cancel", error.message, "error");
    }
  }

  async function uploadStudioFile() {
    const input = $("#studioUpload");
    const file = input?.files?.[0];
    if (!file) {
      toast("Choose a file", "Pick a deliverable in the file picker first.", "error");
      return;
    }
    const button = $("#btnUpload");
    setBusy(button, true);
    try {
      const form = new FormData();
      form.append("file", file);
      const upload = await api("/api/uploads", { method: "POST", body: form });
      state.studio.uploads.push(upload);
      renderUploads();
      input.value = "";
      toast("File uploaded", `${upload.name} is ready to attach.`, "success");
      await refreshState({ quiet: true });
    } catch (error) {
      toast("Upload failed", error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }
  // ---------------------------------------------------------------- utilities
  function setBusy(element, busy) {
    if (!element) return;
    element.classList.toggle("is-loading", Boolean(busy));
    element.disabled = Boolean(busy);
  }

  function scrollToPanel(selector) {
    const panel = $(selector);
    if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function duration(seconds) {
    const total = Math.max(0, Math.round(seconds));
    if (total < 60) return `${total}s`;
    const minutes = Math.floor(total / 60);
    if (minutes < 60) return `${minutes}m ${total % 60}s`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
  }

  function renderTimers() {
    for (const element of $$("[data-expires]")) {
      const timestamp = Date.parse(element.dataset.expires);
      if (Number.isNaN(timestamp)) continue;
      const seconds = (timestamp - Date.now()) / 1000;
      element.innerHTML =
        seconds > 0 ? `expires in <strong>${duration(seconds)}</strong>` : "<strong>expired</strong>";
    }
  }

  // ------------------------------------------------------------ audit actions
  async function verifyChainAction(showToast = true) {
    try {
      const info = await api("/api/audit/verify");
      if (showToast) {
        toast(
          info.ok ? "Audit chain verified" : "Audit chain is broken",
          info.ok
            ? `${info.count} rows checked, every hash links to the previous row.`
            : `Mismatch at row #${info.broken_at ?? "?"}.`,
          info.ok ? "success" : "error"
        );
      }
      await refreshState({ quiet: true });
      return info;
    } catch (error) {
      if (showToast) toast("Could not verify the chain", error.message, "error");
      return null;
    }
  }

  async function tamperAction() {
    try {
      const result = await api("/api/audit/tamper", { method: "POST" });
      await refreshState({ quiet: true });
      if (result.verification?.ok) {
        toast("Tamper test ran", "The chain still verified — try again.", "info");
      } else {
        toast(
          "Tamper detected",
          `Row #${result.tampered_seq} was altered; verification breaks at #${result.verification?.broken_at}.`,
          "error"
        );
      }
      scrollToPanel("#auditPanel");
    } catch (error) {
      toast("Tamper test failed", error.message, "error");
    }
  }

  function switchGridTab(tab) {
    state.grids.tab = tab;
    for (const button of $$(".grid-tab")) {
      const active = button.dataset.tab === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    }
    $("#auditGrid").hidden = tab !== "audit";
    $("#paymentsGrid").hidden = tab !== "payments";
    if (tab === "payments") ensurePaymentsGrid();
    refreshGrids();
    setTimeout(() => {
      const grid = tab === "audit" ? state.grids.audit : state.grids.payments;
      if (grid && typeof grid.sizeColumnsIfNeeded === "function") {
        try {
          grid.sizeColumnsIfNeeded();
        } catch {
          /* grid may not be ready yet */
        }
      }
    }, 60);
  }

  // ----------------------------------------------------------- demo director
  async function resetDemoState({ silent = false } = {}) {
    await api("/api/demo/reset", { method: "POST" });
    state.commandPreview = null;
    state.studio = { uploads: [], result: null, ready: false, milestoneId: null };
    state.demo.stage = 0;
    state.demo.context = {};
    state.demo.pendingApproval = null;
    if (state.demo.approvalResolve) {
      const resolve = state.demo.approvalResolve;
      state.demo.approvalResolve = null;
      resolve("reset");
    }
    $("#commandResult").hidden = true;
    $("#commandResult").innerHTML = "";
    $("#approvalModal").hidden = true;
    state.modal.approval = null;
    renderUploads();
    renderVerificationEmpty();
    applyPackage("concept");
    renderDemoDots();
    await refreshState({ quiet: true });
    if (!silent) toast("Demo reset", "Fresh mandate, fresh hash chain.", "info");
  }

  function demoContext() {
    return state.demo.context;
  }

  async function demoMandate() {
    const result = await api("/api/command", {
      method: "POST",
      body: { text: DEMO_PROMPT, principal: "you", post: false },
    });
    const context = demoContext();
    context.mandateId = result.mandate.id;
    context.plan = result.plan;
    context.parsed = result.parsed;
    context.currency = result.mandate.currency;
    renderCommandResult(result);
    await refreshState({ quiet: true });
    toast(
      "Step 1 · Mandate",
      "The agent parsed a $60 budget and a $40 human-approval threshold.",
      "info"
    );
  }

  async function demoProbe() {
    const context = demoContext();
    if (!context.mandateId) throw new DemoStop("start from step 1.");
    const created = await api("/api/jobs", {
      method: "POST",
      body: {
        mandate_id: context.mandateId,
        title: "Rush logo — overspend probe",
        brief: "The same bakery logo, but rushed for $75. The agent tries to exceed the mandate.",
        category: context.parsed?.category || "design",
        amount_cents: 7500,
        currency: context.currency || "USD",
      },
    });
    const authorization = await api(`/api/jobs/${created.job.id}/authorize`, {
      method: "POST",
      body: {},
    });
    if (authorization.status !== "blocked") {
      throw new Error("The $75 probe was expected to be blocked by the budget.");
    }
    context.probeJobId = created.job.id;
    await refreshState({ quiet: true });
    toast(
      "Step 2 · Hard block",
      "$75 cannot fit a $60 budget. No approval can override the budget cap.",
      "error"
    );
  }

  async function demoJob() {
    const context = demoContext();
    if (!context.mandateId || !context.plan) throw new DemoStop("start from step 1.");
    const plan = context.plan;
    const total = (plan.milestones || []).reduce((sum, item) => sum + Number(item.amount_cents || 0), 0);
    const created = await api("/api/jobs", {
      method: "POST",
      body: {
        mandate_id: context.mandateId,
        title: plan.title,
        brief: context.parsed?.job_brief || DEMO_PROMPT,
        category: context.parsed?.category || "design",
        amount_cents: total,
        currency: context.currency || "USD",
        plan,
      },
    });
    const job = created.job;
    context.jobId = job.id;
    context.milestones = job.milestones || [];
    const authorization = await api(`/api/jobs/${job.id}/authorize`, {
      method: "POST",
      body: {},
    });
    await refreshState({ quiet: true });
    if (authorization.status === "approval_required") {
      context.approvalId = authorization.approval.id;
      state.demo.pendingApproval = new Promise((resolve) => {
        state.demo.approvalResolve = resolve;
      });
      openApprovalModal(authorization.approval);
      toast(
        "Step 3 · Human check",
        "$60 is above your $40 threshold. Approving authorizes the hold — it does not release funds.",
        "info"
      );
      return;
    }
    if (authorization.status === "blocked") {
      throw new Error(`The $60 job was blocked: ${decisionReasons(authorization.decision)}`);
    }
    if (authorization.status === "payment_error") {
      throw new Error(authorization.error || "PayPal failed to authorize.");
    }
  }

  async function demoMilestone(quality) {
    const context = demoContext();
    if (!context.milestones?.length) throw new DemoStop("start from step 1.");
    const index = { concept: 0, draft: 1, final: 1, brandkit: 2 }[quality];
    const milestone = context.milestones[index];
    if (!milestone) throw new Error(`Milestone ${index} is missing from the job.`);
    const submission = await api(`/api/demo/milestones/${milestone.id}/submit`, {
      method: "POST",
      body: { quality },
    });
    const result = await api(`/api/submissions/${submission.id}/verify`, { method: "POST" });
    await refreshState({ quiet: true });
    state.studio.result = result;
    state.studio.milestoneId = milestone.id;
    const select = $("#studioMilestone");
    renderStudioOptions();
    if (select && feasibleMilestones().some((item) => item.milestone.id === milestone.id)) {
      select.value = milestone.id;
    }
    $("#studioQuality").value = quality;
    applyPackage(quality);
    renderVerification(result);
    renderJobs();
    scrollToPanel("#verifyPanel");
    const verdict = result.verification?.verdict;
    const labels = {
      concept: "Step 4 · Concept board",
      draft: "Step 5 · Weak draft",
      final: "Step 6 · Logo master",
      brandkit: "Step 7 · Brand kit",
    };
    toast(
      `${labels[quality]}: ${verdict}`,
      result.verification?.summary || "",
      verdict === "pass" ? "success" : verdict === "revise" ? "error" : "info"
    );
    return verdict;
  }

  async function demoConcept() {
    const verdict = await demoMilestone("concept");
    if (verdict !== "pass") throw new Error(`concept board should pass, got ${verdict}`);
  }

  async function demoDraft() {
    const verdict = await demoMilestone("draft");
    if (verdict !== "revise") throw new Error(`the placeholder draft should be rejected, got ${verdict}`);
  }

  async function demoFinal() {
    const verdict = await demoMilestone("final");
    if (verdict !== "pass") throw new Error(`the final logo should pass, got ${verdict}`);
  }

  async function demoBrandkit() {
    const verdict = await demoMilestone("brandkit");
    if (verdict !== "pass") throw new Error(`the brand kit should pass, got ${verdict}`);
  }

  async function demoAudit() {
    const info = await verifyChainAction(false);
    if (!info?.ok) throw new Error("The audit chain did not verify.");
    await refreshState({ quiet: true });
    toast(
      "Step 8 · Audit chain",
      `${info.count} rows verified. Every decision links to the previous SHA-256 hash.`,
      "success"
    );
    scrollToPanel("#auditPanel");
  }

  async function runDemoStage(key) {
    switch (key) {
      case "mandate": return demoMandate();
      case "probe": return demoProbe();
      case "job": return demoJob();
      case "concept": return demoConcept();
      case "draft": return demoDraft();
      case "final": return demoFinal();
      case "brandkit": return demoBrandkit();
      case "audit": return demoAudit();
      default: throw new Error(`Unknown demo stage: ${key}`);
    }
  }

  function setDemoBusy(busy) {
    state.demo.running = Boolean(busy);
    const run = $("#btnDemoRun");
    const step = $("#btnDemoStep");
    const reset = $("#btnDemoReset");
    if (run) {
      run.classList.toggle("is-loading", Boolean(busy));
      run.disabled = Boolean(busy);
    }
    if (step) step.disabled = Boolean(busy);
    if (reset) reset.disabled = Boolean(busy);
    renderDemoDots();
  }

  function setDemoStatus(text) {
    const host = $("#demoStatus");
    if (host) host.textContent = text;
  }

  function renderDemoDots() {
    const host = $("#demoDots");
    if (host) {
      host.innerHTML = DEMO_STAGES.map((_, index) => {
        const className = index < state.demo.stage ? "is-done" : index === state.demo.stage && state.demo.running ? "is-active" : "";
        return `<span class="demo-dot ${className}"></span>`;
      }).join("");
    }
    const title = $("#demoTitle");
    if (!title) return;
    if (state.demo.running) {
      const stage = DEMO_STAGES[state.demo.stage];
      title.textContent = stage ? `Step ${state.demo.stage + 1}/${DEMO_STAGES.length} · ${stage.title}` : "Running demo…";
    } else if (state.demo.stage >= DEMO_STAGES.length) {
      title.textContent = "Demo complete";
    } else if (state.demo.stage > 0) {
      title.textContent = `Paused after step ${state.demo.stage}`;
    } else {
      title.textContent = "Ready when you are";
    }
  }

  async function runFullDemo() {
    if (state.demo.running) return;
    setDemoBusy(true);
    setDemoStatus("Resetting the demo…");
    try {
      await resetDemoState({ silent: true });
      for (let index = 0; index < DEMO_STAGES.length; index += 1) {
        state.demo.stage = index;
        renderDemoDots();
        const stage = DEMO_STAGES[index];
        setDemoStatus(stage.status);
        await runDemoStage(stage.key);
        if (stage.key === "job" && state.demo.pendingApproval) {
          const decision = await state.demo.pendingApproval;
          state.demo.pendingApproval = null;
          if (decision !== "approved") {
            throw new DemoStop("the human approval was not granted.");
          }
        }
        state.demo.stage = index + 1;
        renderDemoDots();
        await wait(560);
      }
      setDemoStatus("Complete: every milestone verified, released, paid out and hash-chained.");
      toast(
        "Demo complete",
        "Three milestones verified. $60 captured. Payouts recorded. Audit chain intact.",
        "success"
      );
      scrollToPanel("#auditPanel");
    } catch (error) {
      if (error instanceof DemoStop) {
        setDemoStatus(`Paused — ${error.message}`);
      } else {
        console.error(error);
        toast("Demo stopped", error.message, "error");
        setDemoStatus(`Stopped: ${error.message}`);
      }
    } finally {
      setDemoBusy(false);
    }
  }

  async function stepDemo() {
    if (state.demo.running) return;
    if (state.demo.stage >= DEMO_STAGES.length) {
      await resetDemoState({ silent: true });
      toast("Demo reset", "Ready for another run.", "info");
      return;
    }
    if (state.demo.stage === 0 && !state.demo.context.mandateId) {
      await resetDemoState({ silent: true });
    }
    const stage = DEMO_STAGES[state.demo.stage];
    setDemoBusy(true);
    setDemoStatus(stage.status);
    try {
      await runDemoStage(stage.key);
      state.demo.stage += 1;
      if (stage.key === "job" && state.demo.pendingApproval) {
        setDemoStatus("Waiting for your approval in the modal. Then click Step again.");
      } else {
        setDemoStatus("Step complete. Click Step to continue, or Run full demo from the start.");
      }
    } catch (error) {
      if (error instanceof DemoStop) setDemoStatus(`Paused — ${error.message}`);
      else {
        console.error(error);
        toast("Step failed", error.message, "error");
        setDemoStatus(`Stopped: ${error.message}`);
      }
    } finally {
      setDemoBusy(false);
    }
  }

  // ------------------------------------------------------------------ events
  function bindEvents() {
    $("#commandForm")?.addEventListener("submit", submitCommand);
    $("#commandText")?.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        $("#commandForm")?.requestSubmit();
      }
    });
    for (const chip of $$("[data-example]")) {
      chip.addEventListener("click", () => {
        $("#commandText").value = chip.dataset.example;
        $("#commandText").focus();
      });
    }
    $("#spendForm")?.addEventListener("submit", checkSpend);
    $("#btnReset")?.addEventListener("click", () => {
      resetDemoState().catch((error) => toast("Reset failed", error.message, "error"));
    });
    $("#btnDemoReset")?.addEventListener("click", () => {
      resetDemoState().catch((error) => toast("Reset failed", error.message, "error"));
    });
    $("#btnVerifyChain")?.addEventListener("click", () => verifyChainAction(true));
    $("#btnTamper")?.addEventListener("click", tamperAction);
    $("#btnDemoRun")?.addEventListener("click", runFullDemo);
    $("#btnDemoStep")?.addEventListener("click", stepDemo);
    $("#btnApprove")?.addEventListener("click", () => decideApproval(true));
    $("#btnReject")?.addEventListener("click", () => decideApproval(false));
    $("#btnModalClose")?.addEventListener("click", () => closeApprovalModal("dismissed"));
    $("#approvalModal")?.addEventListener("click", (event) => {
      if (event.target === $("#approvalModal")) closeApprovalModal("dismissed");
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !$("#approvalModal").hidden) {
        closeApprovalModal("dismissed");
      }
    });
    for (const tab of $$(".grid-tab")) {
      tab.addEventListener("click", () => switchGridTab(tab.dataset.tab));
    }
    $("#gridSearch")?.addEventListener("input", (event) => {
      state.grids.search = event.target.value;
      for (const grid of [state.grids.audit, state.grids.payments]) {
        if (grid) grid.setGridOption("quickFilterText", state.grids.search);
      }
    });
    $("#decisionFilter")?.addEventListener("change", (event) => {
      state.grids.decision = event.target.value;
      refreshGrids();
    });
    $("#studioQuality")?.addEventListener("change", (event) => applyPackage(event.target.value));
    $("#studioMilestone")?.addEventListener("change", (event) => {
      state.studio.milestoneId = event.target.value || null;
      renderJobs();
    });
    $("#btnUpload")?.addEventListener("click", uploadStudioFile);
    $("#btnSubmitWork")?.addEventListener("click", () => submitStudio(false));
    $("#btnSubmitVerify")?.addEventListener("click", () => submitStudio(true));

    document.addEventListener("click", async (event) => {
      const trigger = event.target.closest("[data-action]");
      if (!trigger) return;
      const action = trigger.dataset.action;
      try {
        if (action === "post-plan") {
          await postPreviewPlan(trigger);
        } else if (action === "authorize-job") {
          await authorizeJobRequest(trigger.dataset.job);
        } else if (action === "review-job-approval") {
          const job = findJob(trigger.dataset.job);
          const approval = (state.data?.approvals || []).find(
            (item) => item.status === "pending" && item.job_id === trigger.dataset.job
          );
          if (approval) openApprovalModal(approval);
          else toast("No pending approval", job ? "This job is not waiting on you." : "Job not found.", "info");
        } else if (action === "cancel-job") {
          await cancelJobAction(trigger.dataset.job);
        } else if (action === "verify-submission") {
          await verifySubmissionId(trigger.dataset.sub);
        } else if (action === "release-milestone") {
          await releaseMilestoneAction(trigger.dataset.ms);
        } else if (action === "use-milestone") {
          selectMilestone(trigger.dataset.ms);
        } else if (action === "open-approval") {
          const approval = (state.data?.approvals || []).find((item) => item.id === trigger.dataset.approval);
          if (approval) openApprovalModal(approval);
        } else if (action === "reject-approval") {
          await quickReject(trigger.dataset.approval);
        } else if (action === "remove-upload") {
          state.studio.uploads = state.studio.uploads.filter((item) => item.id !== trigger.dataset.upload);
          renderUploads();
        } else if (action === "open-mandate") {
          scrollToPanel("#mandatePanel");
        }
      } catch (error) {
        toast("Action failed", error.message, "error");
      }
    });
  }

  // -------------------------------------------------------------------- init
  function init() {
    bindEvents();
    renderDemoDots();
    renderUploads();
    renderVerificationEmpty();
    applyPackage("concept");
    setConnection("polling", "connecting");
    connectStream();
    refreshState().catch(() => {});
    state.pollTimer = setInterval(() => {
      refreshState({ quiet: true }).catch(() => {});
    }, 8000);
    state.clockTimer = setInterval(renderTimers, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
