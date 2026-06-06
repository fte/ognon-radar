const API_BASE_URL = "http://api.dw.13h.be";
const POLL_DELAY_MS = 2500;

const form = document.querySelector("#search-form");
const submitButton = form.querySelector("button");
const statusOutput = document.querySelector("#status");
const jobDetails = document.querySelector("#job-details");
const jobId = document.querySelector("#job-id");
const jobStatus = document.querySelector("#job-status");
const jobStarted = document.querySelector("#job-started");
const jobCompleted = document.querySelector("#job-completed");
const summary = document.querySelector("#summary");
const results = document.querySelector("#results");

let activePoll = null;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  window.clearTimeout(activePoll);

  const payload = Object.fromEntries(new FormData(form));
  payload.term = payload.term.trim();
  payload.max_results = Number(payload.max_results);
  payload.max_depth = Number(payload.max_depth);
  payload.max_pages = Number(payload.max_pages);
  payload.timeout = 30;

  setBusy(true);
  setStatus("Envoi de la recherche...", "running");
  resetJob();

  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/search`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Client-ID": "clients-www-demo",
      },
      body: JSON.stringify(payload),
    });

    const created = await readJson(response);
    if (!response.ok) {
      throw new Error(formatApiError(created, response.status));
    }

    setStatus("Job cree. Recherche en cours...", "running");
    updateJob(created);
    pollJob(created.job_id);
  } catch (error) {
    setBusy(false);
    setStatus(error.message, "failed");
  }
});

async function pollJob(id) {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/jobs/${encodeURIComponent(id)}?limit=100`);
    const job = await readJson(response);

    if (!response.ok) {
      throw new Error(formatApiError(job, response.status));
    }

    updateJob(job);

    if (job.status === "completed") {
      setBusy(false);
      setStatus("Job termine.", "completed");
      renderResults(job.result);
      return;
    }

    if (job.status === "failed" || job.status === "cancelled") {
      setBusy(false);
      setStatus(job.error || `Job ${job.status}.`, job.status);
      return;
    }

    setStatus(statusLabel(job.status), "running");
    activePoll = window.setTimeout(() => pollJob(id), POLL_DELAY_MS);
  } catch (error) {
    setBusy(false);
    setStatus(error.message, "failed");
  }
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
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
  submitButton.textContent = isBusy ? "Recherche..." : "Rechercher";
}

function setStatus(message, state) {
  statusOutput.value = message;
  statusOutput.dataset.state = state;
}

function resetJob() {
  jobDetails.hidden = true;
  jobId.textContent = "";
  jobStatus.textContent = "";
  jobStarted.textContent = "";
  jobCompleted.textContent = "";
  summary.textContent = "Les resultats apparaitront quand le job sera termine.";
  results.replaceChildren();
}

function updateJob(job) {
  jobDetails.hidden = false;
  jobId.textContent = job.job_id || job.id || "";
  jobStatus.textContent = statusLabel(job.status);
  jobStarted.textContent = formatDate(job.started_at || job.created_at);
  jobCompleted.textContent = formatDate(job.completed_at);
}

function statusLabel(status) {
  const labels = {
    queued: "En attente...",
    running: "Recherche en cours...",
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
  footer.textContent = `Occurrences: ${item.term_count} · profondeur: ${item.depth}`;

  article.append(title, snippet, footer);
  row.append(article);
  return row;
}
