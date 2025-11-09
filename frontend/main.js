const storageKeys = {
  baseUrl: "scheduler.console.baseUrl",
  defaultUserId: "scheduler.console.defaultUserId",
};

const baseUrlInput = document.getElementById("base-url");
const defaultUserIdInput = document.getElementById("default-user-id");

const loadingTemplate = document.getElementById("loading-template");

function loadPreferences() {
  const storedUrl = window.localStorage.getItem(storageKeys.baseUrl);
  if (storedUrl) {
    baseUrlInput.value = storedUrl;
  }
  const storedUser = window.localStorage.getItem(storageKeys.defaultUserId);
  if (storedUser) {
    defaultUserIdInput.value = storedUser;
  }
}

function savePreferences(event) {
  event.preventDefault();
  window.localStorage.setItem(storageKeys.baseUrl, baseUrlInput.value.trim());
  window.localStorage.setItem(storageKeys.defaultUserId, defaultUserIdInput.value.trim());
  showToast("Configuration saved");
}

function getBaseUrl() {
  const value = baseUrlInput.value.trim();
  if (!value) {
    throw new Error("Please configure the API base URL first.");
  }
  return value.replace(/\/$/, "");
}

function getDefaultUserId() {
  return defaultUserIdInput.value.trim();
}

function showToast(message, tone = "info") {
  const toast = document.createElement("div");
  toast.className = `toast toast-${tone}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.add("visible");
  });
  setTimeout(() => {
    toast.classList.remove("visible");
    setTimeout(() => toast.remove(), 300);
  }, 2500);
}

function setLoading(target) {
  if (!target) return () => {};
  const loader = loadingTemplate.content.cloneNode(true);
  target.dataset.previous = target.textContent;
  target.textContent = loader.textContent;
  return () => {
    target.textContent = target.dataset.previous || "";
    delete target.dataset.previous;
  };
}

async function request(path, { method = "GET", headers = {}, body = undefined } = {}) {
  const baseUrl = getBaseUrl();
  const url = `${baseUrl}${path}`;
  const defaultHeaders = { "Accept": "application/json" };
  if (body !== undefined && !(body instanceof FormData)) {
    defaultHeaders["Content-Type"] = "application/json";
  }
  const mergedHeaders = { ...defaultHeaders, ...headers };

  const response = await fetch(url, { method, headers: mergedHeaders, body });
  const contentType = response.headers.get("content-type") || "";
  let payload;
  if (contentType.includes("application/json")) {
    payload = await response.json().catch(() => response.text());
  } else {
    payload = await response.text();
  }
  if (!response.ok) {
    throw { status: response.status, payload };
  }
  return payload;
}

function renderResult(target, data) {
  if (!target) return;
  if (data === undefined || data === null || data === "") {
    target.textContent = "(empty response)";
    return;
  }
  if (typeof data === "string") {
    target.textContent = data;
    return;
  }
  target.textContent = JSON.stringify(data, null, 2);
}

function renderError(target, error) {
  if (!target) return;
  if (error?.status) {
    target.textContent = JSON.stringify({ status: error.status, body: error.payload }, null, 2);
  } else if (error instanceof Error) {
    target.textContent = error.message;
  } else {
    target.textContent = JSON.stringify(error, null, 2);
  }
}

function parseJsonField(value) {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  try {
    return JSON.parse(trimmed);
  } catch (error) {
    throw new Error("Consent must be valid JSON");
  }
}

function parseCommaSeparatedIntegers(value) {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  return trimmed
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const parsed = Number.parseInt(part, 10);
      if (Number.isNaN(parsed)) {
        throw new Error(`Invalid number in days_of_week: ${part}`);
      }
      return parsed;
    });
}

function randomKey() {
  return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
}

function prepareHeaders({ userId, extraHeaders }) {
  const headers = { ...extraHeaders };
  const defaultUser = getDefaultUserId();
  const effectiveUserId = userId?.trim() || defaultUser;
  if (effectiveUserId) {
    headers["X-User-Id"] = effectiveUserId;
  }
  return headers;
}

loadPreferences();

document.getElementById("config-form").addEventListener("submit", savePreferences);

const forms = {
  dates: {
    form: document.getElementById("dates-form"),
    result: document.getElementById("dates-result"),
    async handle(event) {
      event.preventDefault();
      const formData = new FormData(this.form);
      const params = new URLSearchParams();
      for (const [key, value] of formData.entries()) {
        if (!value) continue;
        params.append(key, value);
      }
      const query = params.toString();
      const payload = await request(`/v1/dates${query ? `?${query}` : ""}`);
      renderResult(this.result, payload);
    },
  },
  slots: {
    form: document.getElementById("slots-form"),
    result: document.getElementById("slots-result"),
    async handle(event) {
      event.preventDefault();
      const formData = new FormData(this.form);
      const params = new URLSearchParams();
      ["date", "location_id", "service_id", "stylist_id", "timezone"].forEach((key) => {
        const value = formData.get(key);
        if (value) params.append(key, value);
      });
      const query = params.toString();
      const payload = await request(`/v1/slots?${query}`);
      renderResult(this.result, payload);
    },
  },
  booking: {
    form: document.getElementById("booking-form"),
    result: document.getElementById("booking-result"),
    async handle(event) {
      event.preventDefault();
      const formData = new FormData(this.form);
      const consent = parseJsonField(formData.get("consent") || "");
      const idempotencyKey = (formData.get("idempotency_key") || "").trim() || randomKey();
      const headers = prepareHeaders({
        userId: formData.get("user_id"),
        extraHeaders: { "Idempotency-Key": idempotencyKey },
      });
      const payload = {
        slot_id: formData.get("slot_id"),
        customer: {
          name: formData.get("customer_name"),
          phone: formData.get("customer_phone"),
          email: formData.get("customer_email") || undefined,
        },
        notes: formData.get("notes") || undefined,
        consent,
        source: formData.get("source") || undefined,
      };
      const response = await request("/v1/bookings", {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      renderResult(this.result, { ...response, idempotency_key: idempotencyKey });
    },
  },
  confirm: {
    form: document.getElementById("confirm-form"),
    result: document.getElementById("confirm-result"),
    async handle(event) {
      event.preventDefault();
      const formData = new FormData(this.form);
      const bookingId = formData.get("booking_id");
      const payload = await request(`/v1/bookings/${encodeURIComponent(bookingId)}/confirm`, {
        method: "POST",
      });
      renderResult(this.result, payload);
    },
  },
  availability: {
    form: document.getElementById("availability-form"),
    result: document.getElementById("availability-result"),
    async handle(event) {
      event.preventDefault();
      const formData = new FormData(this.form);
      const payload = {
        location_id: formData.get("location_id"),
        service_id: formData.get("service_id") || undefined,
        stylist_id: formData.get("stylist_id") || undefined,
        rule_kind: formData.get("rule_kind"),
        days_of_week: parseCommaSeparatedIntegers(formData.get("days_of_week") || ""),
        start_time: formData.get("start_time"),
        end_time: formData.get("end_time"),
        slot_capacity: Number.parseInt(formData.get("slot_capacity"), 10),
        slot_granularity_minutes: Number.parseInt(formData.get("slot_granularity_minutes"), 10),
        valid_from: formData.get("valid_from") || undefined,
        valid_to: formData.get("valid_to") || undefined,
        is_closed: formData.get("is_closed") === "on",
      };
      const response = await request("/admin/v1/availabilities", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderResult(this.result, response);
    },
  },
  generate: {
    form: document.getElementById("generate-form"),
    result: document.getElementById("generate-result"),
    async handle(event) {
      event.preventDefault();
      const formData = new FormData(this.form);
      const payload = {
        location_id: formData.get("location_id"),
        from: formData.get("from"),
        to: formData.get("to"),
        dry_run: formData.get("dry_run") === "on",
      };
      const response = await request("/admin/v1/slots/generate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderResult(this.result, response);
    },
  },
  bookings: {
    form: document.getElementById("bookings-form"),
    result: document.getElementById("bookings-result"),
    async handle(event) {
      event.preventDefault();
      const formData = new FormData(this.form);
      const params = new URLSearchParams();
      for (const [key, value] of formData.entries()) {
        if (!value) continue;
        params.append(key, value);
      }
      const query = params.toString();
      const payload = await request(`/admin/v1/bookings${query ? `?${query}` : ""}`);
      renderResult(this.result, payload);
    },
  },
  bookingUpdate: {
    form: document.getElementById("booking-update-form"),
    result: document.getElementById("booking-update-result"),
    async handle(event) {
      event.preventDefault();
      const formData = new FormData(this.form);
      const bookingId = formData.get("booking_id");
      const action = formData.get("action");
      const payload = {
        action,
        reason: formData.get("reason") || undefined,
        new_slot_id: formData.get("new_slot_id") || undefined,
      };
      const response = await request(`/admin/v1/bookings/${encodeURIComponent(bookingId)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      renderResult(this.result, response);
    },
  },
};

Object.values(forms).forEach(({ form, result, handle }) => {
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    const resetLoading = setLoading(result);
    try {
      await handle.call({ form, result }, event);
    } catch (error) {
      renderError(result, error);
      const message = error?.message || (error?.status ? `Request failed (${error.status})` : "Unexpected error");
      showToast(message, "error");
    } finally {
      resetLoading();
    }
  });
});

showToast("Ready when you are ✂️");
