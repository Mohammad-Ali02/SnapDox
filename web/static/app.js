/* SnapDox front-end.
 *
 * One card per queued file.  The target dropdown is filled from
 * /api/targets/<ext>, so the browser only ever offers conversions the
 * registry can actually perform.
 */

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const queue = document.getElementById("queue");

const POLL_MS = 700;
const targetCache = new Map();

/* ---------- helpers ---------- */

const extensionOf = (name) => {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function currentOptions() {
  return {
    dpi: document.getElementById("opt-dpi").value,
    pages: document.getElementById("opt-pages").value,
    quality: document.getElementById("opt-quality").value,
    trace_mode: document.getElementById("opt-trace-mode").value,
    trace_speckle: document.getElementById("opt-speckle").value,
    pdf_layout: document.getElementById("opt-pdf-layout").value,
    password: document.getElementById("opt-password").value,
  };
}

async function loadTargets(ext) {
  if (targetCache.has(ext)) return targetCache.get(ext);
  const response = await fetch(`/api/targets/${encodeURIComponent(ext)}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "unsupported format");
  targetCache.set(ext, data.groups);
  return data.groups;
}

/* ---------- one file, one card ---------- */

function addCard(file) {
  const card = el("div", "card");
  const name = el("div", "card-name", file.name);
  const controls = el("div", "controls");
  const meta = el("p", "card-meta", "");

  card.append(name, controls, meta);
  queue.append(card);

  const ext = extensionOf(file.name);
  const select = el("select");
  const button = el("button", null, "Convert");
  button.disabled = true;

  const remove = el("button", "ghost", "✕");
  remove.title = "Remove from queue";
  remove.addEventListener("click", () => card.remove());

  controls.append(select, button, remove);

  loadTargets(ext)
    .then((groups) => {
      if (!groups.length) throw new Error(`.${ext} files can't be converted`);
      for (const group of groups) {
        const optgroup = el("optgroup");
        optgroup.label = group.kind;
        for (const option of group.options) {
          const node = el("option", null, `${option.ext.toUpperCase()} — ${option.label}`);
          node.value = option.ext;
          node.dataset.note = option.note || "";
          node.dataset.direct = option.direct;
          node.dataset.fanOut = option.fanOut;
          optgroup.append(node);
        }
        select.append(optgroup);
      }
      button.disabled = false;
      describeChoice();
    })
    .catch((error) => {
      meta.className = "card-meta error";
      meta.textContent = error.message;
      select.remove();
      button.remove();
    });

  function describeChoice() {
    const option = select.selectedOptions[0];
    if (!option) return;
    const parts = [];
    if (option.dataset.direct !== "true") parts.push("two-step conversion");
    if (option.dataset.note) parts.push(option.dataset.note);
    if (option.dataset.fanOut === "true") parts.push("one file per page, downloaded as a zip");
    meta.className = "card-meta";
    meta.textContent = parts.join(" · ");
  }

  select.addEventListener("change", describeChoice);
  button.addEventListener("click", () => startConversion(file, select.value, { card, controls, meta }));
}

/* ---------- conversion ---------- */

async function startConversion(file, target, ui) {
  const { controls, meta } = ui;

  const body = new FormData();
  body.append("file", file);
  body.append("target", target);
  for (const [key, value] of Object.entries(currentOptions())) body.append(key, value);

  controls.replaceChildren(el("div", "spinner"), el("span", "badge", "converting"));
  meta.className = "card-meta";
  meta.textContent = `Converting to ${target.toUpperCase()}…`;

  let job;
  try {
    const response = await fetch("/api/convert", { method: "POST", body });
    job = await response.json();
    if (!response.ok) throw new Error(job.error || "conversion failed to start");
  } catch (error) {
    return showError(ui, error.message);
  }

  poll(job.id, ui);
}

function poll(jobId, ui) {
  const tick = async () => {
    let job;
    try {
      const response = await fetch(`/api/job/${jobId}`);
      if (!response.ok) throw new Error("lost track of this job");
      job = await response.json();
    } catch (error) {
      return showError(ui, error.message);
    }

    if (job.status === "done") return showDone(job, ui);
    if (job.status === "error") return showError(ui, job.message, job.hint);
    setTimeout(tick, POLL_MS);
  };
  setTimeout(tick, POLL_MS);
}

function showDone(job, { controls, meta }) {
  const link = el("a", "download", job.bundled ? `Download ${job.files.length} files` : "Download");
  link.href = job.download;
  controls.replaceChildren(link);

  const detail = [job.route, `${job.seconds}s`];
  if (job.bundled) detail.push(`${job.files.length} files, zipped`);
  meta.className = "card-meta";
  meta.textContent = detail.join(" · ");
}

function showError({ controls, meta }, message, hint) {
  controls.replaceChildren(el("span", "badge err", "failed"));
  meta.className = "card-meta error";
  meta.textContent = hint ? `${message} — ${hint}` : message;
}

/* ---------- input plumbing ---------- */

const accept = (files) => Array.from(files).forEach(addCard);

const openFilePicker = (event) => {
  // Never re-enter: a click that came from the input itself must not ask the
  // input to click again, or the browser suppresses the picker entirely.
  if (event && event.target === fileInput) return;
  fileInput.click();
};

dropzone.addEventListener("click", openFilePicker);
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    openFilePicker();
  }
});

fileInput.addEventListener("change", () => {
  accept(fileInput.files);
  fileInput.value = "";
});

let dragDepth = 0;
["dragenter", "dragover"].forEach((type) =>
  document.addEventListener(type, (event) => {
    event.preventDefault();
    if (type === "dragenter") dragDepth += 1;
    dropzone.classList.add("dragging");
  })
);

document.addEventListener("dragleave", (event) => {
  event.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dropzone.classList.remove("dragging");
});

document.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  dropzone.classList.remove("dragging");
  if (event.dataTransfer?.files?.length) accept(event.dataTransfer.files);
});
