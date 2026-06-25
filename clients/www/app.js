const API_BASE_URL = "http://api.dw.13h.be";
const CLIENT_ID_KEY = "ognon-client-id";
const API_KEY_KEY = "ognon-api-key";
const POLL_DELAY_MS = 1000;

let clientId = localStorage.getItem(CLIENT_ID_KEY) || generateClientId();
localStorage.setItem(CLIENT_ID_KEY, clientId);

let storedApiKey = localStorage.getItem(API_KEY_KEY) || null;

const endpoints = [
  {
    key: "health",
    method: "GET",
    path: "/api/v1/health",
    title: "Verifier l'API",
    description: "Confirme que le service repond avant de lancer le scenario.",
    mode: "live",
  },
  {
    key: "search",
    method: "POST",
    path: "/api/v1/search",
    title: "Creer un job",
    description: "Envoie la recherche et recupere un job_id.",
    mode: "live",
  },
  {
    key: "job",
    method: "GET",
    path: "/api/v1/jobs/{job_id}",
    title: "Poller le job",
    description: "Interroge le statut chaque seconde jusqu'a l'etat final.",
    mode: "live",
  },
  {
    key: "stream",
    method: "GET",
    path: "/api/v1/jobs/{job_id}/stream",
    title: "Alternative SSE",
    description: "Flux temps reel disponible cote API, montre ici comme option.",
    mode: "example",
  },
  {
    key: "jobs",
    method: "GET",
    path: "/api/v1/jobs",
    title: "Lister les jobs",
    description: "Vue paginee des recherches connues, filtrable par client/statut.",
    mode: "example",
  },
  {
    key: "cancel",
    method: "POST",
    path: "/api/v1/jobs/{job_id}/cancel",
    title: "Annuler",
    description: "Action possible uniquement quand le job est encore en attente.",
    mode: "example",
  },
  {
    key: "delete",
    method: "DELETE",
    path: "/api/v1/jobs/{job_id}",
    title: "Nettoyer",
    description: "Suppression d'un job terminal, presentee sans execution.",
    mode: "example",
  },
  {
    key: "webhooks",
    method: "PUT/GET",
    path: "/api/v1/webhooks/config",
    title: "Webhooks",
    description: "Configurer et consulter les notifications par client.",
    mode: "example",
  },
];

const form = document.querySelector("#search-form");
const submitButton = form.querySelector("button");
const healthStatus = document.querySelector("#health-status");
const endpointList = document.querySelector("#endpoint-list");
const statusOutput = document.querySelector("#status");
const jobMeter = document.querySelector("#job-meter");
const clientIdEl = document.querySelector("#client-id");
const clientIdFull = document.querySelector("#client-id-full");
const apiKeyStatus = document.querySelector("#api-key-status");
const apiKeyDisplay = document.querySelector("#api-key-display");
const apiKeyInput = document.querySelector("#api-key-input");
const credKeyActive = document.querySelector("#cred-key-active");
const credKeyInactive = document.querySelector("#cred-key-inactive");
const jobId = document.querySelector("#job-id");
const jobStatus = document.querySelector("#job-status");
const jobStarted = document.querySelector("#job-started");
const jobCompleted = document.querySelector("#job-completed");
const summary = document.querySelector("#summary");
const results = document.querySelector("#results");
const pollLog = document.querySelector("#poll-log");

let activePoll = null;
let pollCount = 0;

renderEndpoints();
checkHealth();
renderClientId();
renderCredentials();

document.querySelector("#copy-client-id").addEventListener("click", () => {
  navigator.clipboard.writeText(clientId).catch(() => {});
});

document.querySelector("#reset-client-id").addEventListener("click", () => {
  localStorage.removeItem(CLIENT_ID_KEY);
  location.reload();
});

document.querySelector("#apply-api-key").addEventListener("click", () => {
  const value = apiKeyInput.value.trim();
  if (!value) return;
  storedApiKey = value;
  localStorage.setItem(API_KEY_KEY, storedApiKey);
  apiKeyInput.value = "";
  renderCredentials();
});

document.querySelector("#generate-api-key").addEventListener("click", generateClientKey);

document.querySelector("#clear-api-key").addEventListener("click", () => {
  storedApiKey = null;
  localStorage.removeItem(API_KEY_KEY);
  renderCredentials();
});

healthStatus.addEventListener("click", () => {
  const href = healthStatus.dataset.href;
  if (href) window.open(href, "_blank", "noreferrer");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  window.clearTimeout(activePoll);

  const payload = Object.fromEntries(new FormData(form));
  payload.term = payload.term.trim();
  payload.max_results = Number(payload.max_results);
  payload.max_depth = Number(payload.max_depth);
  payload.max_pages = Number(payload.max_pages);
  payload.timeout = 30;

  resetScenario();
  setBusy(true);
  setEndpointState("search", "active");
  setStatus("Envoi de la recherche...", "running");

  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getClientHeaders() },
      body: JSON.stringify(payload),
    });

    const created = await readJson(response);
    if (!response.ok) {
      throw new Error(formatApiError(created, response.status));
    }

    setEndpointState("search", "done");
    setEndpointState("job", "active");
    setStatus("Job cree. Polling toutes les secondes...", "running");
    updateJob(created);
    addPollLog("POST", "/api/v1/search", created.status, created.job_id);
    pollJob(created.job_id);
  } catch (error) {
    failScenario(error);
  }
});

async function checkHealth() {
  setEndpointState("health", "active");

  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/health`);
    const health = await readJson(response);

    if (!response.ok) {
      throw new Error(formatApiError(health, response.status));
    }

    healthStatus.value = "API disponible — docs";
    healthStatus.dataset.state = "completed";
    healthStatus.dataset.href = `${API_BASE_URL}/docs`;
    setEndpointState("health", "done");
  } catch (error) {
    healthStatus.value = error.message;
    healthStatus.dataset.state = "failed";
    setEndpointState("health", "failed");
  }
}

async function pollJob(id) {
  pollCount += 1;

  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/jobs/${encodeURIComponent(id)}?limit=100`, {
      headers: getClientHeaders(),
    });
    const job = await readJson(response);

    if (!response.ok) {
      throw new Error(formatApiError(job, response.status));
    }

    updateJob(job);
    addPollLog("GET", `/api/v1/jobs/${shortId(id)}`, job.status, `poll #${pollCount}`);

    if (job.status === "completed") {
      setBusy(false);
      setEndpointState("job", "done");
      setStatus("Job termine. Resultats charges.", "completed");
      setMeterState("completed");
      renderResults(job.result);
      return;
    }

    if (job.status === "failed" || job.status === "cancelled") {
      setBusy(false);
      setEndpointState("job", "failed");
      setStatus(job.error || `Job ${job.status}.`, job.status);
      setMeterState("failed");
      return;
    }

    setStatus(statusLabel(job.status), "running");
    setMeterState(job.status);
    activePoll = window.setTimeout(() => pollJob(id), POLL_DELAY_MS);
  } catch (error) {
    failScenario(error);
  }
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function renderEndpoints() {
  endpointList.replaceChildren(...endpoints.map((endpoint) => {
    const item = document.createElement("li");
    const method = document.createElement("span");
    const body = document.createElement("div");
    const title = document.createElement("strong");
    const path = document.createElement("code");
    const description = document.createElement("p");

    item.dataset.key = endpoint.key;
    item.dataset.state = "idle";
    item.dataset.mode = endpoint.mode;
    method.className = "method";
    method.textContent = endpoint.method;
    title.textContent = endpoint.title;
    path.textContent = endpoint.path;
    description.textContent = endpoint.description;

    body.append(title, path, description);
    item.append(method, body);
    return item;
  }));
}

function setEndpointState(key, state) {
  const item = endpointList.querySelector(`[data-key="${key}"]`);
  if (item) {
    item.dataset.state = state;
  }
}

function formatApiError(body, status) {
  if (typeof body.detail === "string") {
    return body.detail;
  }

  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => item.msg || "Erreur de validation").join(" ");
  }

  return `Erreur API ${status}.`;
}

function setBusy(isBusy) {
  submitButton.disabled = isBusy;
  submitButton.lastChild.textContent = isBusy ? " Recherche..." : " Lancer le scenario";
}

function setStatus(message, state) {
  statusOutput.value = message;
  statusOutput.dataset.state = state;
}

function setMeterState(state) {
  jobMeter.dataset.state = state || "idle";
}

function generateClientId() {
  const adj = ["swift", "brave", "dark", "silent", "ghost", "sharp", "cold", "wild", "iron", "storm",
               "frost", "amber", "jade", "cobalt", "onyx", "ash", "crimson", "silver", "hollow", "quiet"];
  const noun = ["falcon", "wolf", "raven", "cipher", "spectre", "node", "proxy", "byte", "signal", "relay",
                "vault", "echo", "drift", "torch", "nexus", "pulse", "shard", "arc", "lens", "trace"];
  const buf = crypto.getRandomValues(new Uint32Array(3));
  const pick = (arr, n) => arr[n % arr.length];
  const suffix = buf[2].toString(16).padStart(8, "0").slice(0, 8);
  return `${pick(adj, buf[0])}-${pick(noun, buf[1])}-${suffix}`;
}

function getClientHeaders() {
  if (storedApiKey) {
    return { "X-API-Key": storedApiKey };
  }
  return { "X-Client-ID": clientId };
}

function renderClientId() {
  clientIdEl.textContent = clientId;
  clientIdEl.title = "Conserve en localStorage — reinitialiser pour changer";
}

function renderCredentials() {
  clientIdFull.textContent = clientId;

  if (storedApiKey) {
    const masked = `${storedApiKey.slice(0, 10)}...${storedApiKey.slice(-4)}`;
    apiKeyDisplay.textContent = masked;
    apiKeyDisplay.title = storedApiKey;
    credKeyActive.hidden = false;
    credKeyInactive.hidden = true;
    apiKeyStatus.value = "Cle API active";
    apiKeyStatus.dataset.state = "completed";
  } else {
    credKeyActive.hidden = true;
    credKeyInactive.hidden = false;
    apiKeyStatus.value = "Pas de cle API";
    apiKeyStatus.dataset.state = "";
  }
}

async function generateClientKey() {
  const btn = document.querySelector("#generate-api-key");
  btn.disabled = true;
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/client/key`, {
      method: "POST",
      headers: getClientHeaders(),
    });
    const data = await readJson(response);
    if (!response.ok) {
      throw new Error(formatApiError(data, response.status));
    }
    storedApiKey = data.api_key;
    localStorage.setItem(API_KEY_KEY, storedApiKey);
    renderCredentials();
  } catch (error) {
    alert(`Erreur: ${error.message}`);
  } finally {
    btn.disabled = false;
  }
}

function resetScenario() {
  pollCount = 0;
  for (const endpoint of endpoints) {
    if (endpoint.key !== "health") {
      setEndpointState(endpoint.key, "idle");
    }
  }

  jobId.textContent = "-";
  jobStatus.textContent = "-";
  jobStarted.textContent = "-";
  jobCompleted.textContent = "-";
  pollLog.replaceChildren();
  summary.textContent = "Les resultats apparaitront quand le job sera termine.";
  results.replaceChildren();
  setMeterState("queued");
}

function updateJob(job) {
  const id = job.job_id || job.id || "";
  jobId.textContent = id ? shortId(id) : "-";
  jobId.title = id;
  jobStatus.textContent = statusLabel(job.status);
  jobStarted.textContent = formatDate(job.started_at || job.created_at);
  jobCompleted.textContent = formatDate(job.completed_at);
  setMeterState(job.status);
}

function addPollLog(method, path, state, detail) {
  const row = document.createElement("li");
  const timestamp = document.createElement("time");
  const route = document.createElement("code");
  const status = document.createElement("span");

  timestamp.textContent = new Intl.DateTimeFormat("fr-BE", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date());
  route.textContent = `${method} ${path}`;
  status.textContent = `${statusLabel(state)} - ${detail || ""}`;
  status.dataset.state = state;

  row.append(timestamp, route, status);
  pollLog.prepend(row);

  while (pollLog.children.length > 8) {
    pollLog.lastElementChild.remove();
  }
}

function statusLabel(status) {
  const labels = {
    queued: "En attente",
    running: "Recherche en cours",
    completed: "Termine",
    failed: "Echec",
    cancelled: "Annule",
  };

  return labels[status] || status || "Inconnu";
}

function formatDate(value) {
  if (!value) {
    return "-";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("fr-BE", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(date);
}

function renderResults(result) {
  const items = result?.results || [];
  const total = result?.total ?? items.length;
  const pages = result?.crawled_pages ?? 0;
  const duration = result?.duration_seconds ? `${result.duration_seconds.toFixed(1)} s` : "-";

  summary.textContent = `${total} resultat(s), ${pages} page(s) lue(s), duree ${duration}.`;
  results.replaceChildren(...items.map(renderResult));

  if (items.length === 0) {
    summary.textContent = "Job termine, aucun resultat trouve.";
  }
}

function renderResult(item) {
  const row = document.createElement("li");
  const article = document.createElement("article");
  const title = document.createElement("h3");
  const link = document.createElement("a");
  const snippet = document.createElement("p");
  const footer = document.createElement("footer");

  link.href = item.url;
  link.textContent = item.title || item.url;
  link.rel = "noreferrer";
  title.append(link);

  snippet.textContent = item.snippet || "Aucun extrait disponible.";
  footer.textContent = `Occurrences: ${item.term_count ?? "-"} - profondeur: ${item.depth ?? "-"}`;

  article.append(title, snippet, footer);
  row.append(article);
  return row;
}

function shortId(id) {
  return id.length > 14 ? `${id.slice(0, 8)}...${id.slice(-4)}` : id;
}

function failScenario(error) {
  setBusy(false);
  setStatus(error.message, "failed");
  setMeterState("failed");
}
