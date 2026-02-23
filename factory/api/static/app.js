const ideaInput = document.getElementById("ideaInput");
const buildButton = document.getElementById("buildButton");
const statusBox = document.getElementById("statusBox");
const jobBox = document.getElementById("jobBox");

let pollTimer = null;

function setStatus(text) {
  statusBox.textContent = text;
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function fetchJob(jobId) {
  const resp = await fetch(`/job/${encodeURIComponent(jobId)}`);
  if (!resp.ok) {
    throw new Error(`job request failed: ${resp.status}`);
  }
  return resp.json();
}

function renderJobState(data) {
  const status = data.status || data.state || "unknown";
  const progress = typeof data.progress === "number" ? `${data.progress}%` : "n/a";
  const message = data.message || "";

  setStatus(
    [
      `status: ${status}`,
      `progress: ${progress}`,
      `message: ${message}`,
    ].join("\n")
  );

  if (status === "done" || status === "error") {
    stopPolling();
  }
}

async function startPolling(jobId) {
  stopPolling();

  const pollOnce = async () => {
    try {
      const data = await fetchJob(jobId);
      renderJobState(data);
    } catch (err) {
      setStatus(`poll error: ${err.message}`);
      stopPolling();
    }
  };

  await pollOnce();
  pollTimer = setInterval(pollOnce, 3000);
}

async function submitBuild() {
  const idea = ideaInput.value.trim();
  if (!idea) {
    setStatus("idea is required");
    return;
  }

  buildButton.disabled = true;
  stopPolling();
  setStatus("submitting...");

  try {
    const resp = await fetch("/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idea }),
    });

    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || `build failed: ${resp.status}`);
    }

    const jobId = data.job_id;
    jobBox.textContent = `job_id: ${jobId}`;
    setStatus("queued");
    await startPolling(jobId);
  } catch (err) {
    setStatus(`submit error: ${err.message}`);
  } finally {
    buildButton.disabled = false;
  }
}

buildButton.addEventListener("click", submitBuild);
