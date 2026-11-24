"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false, preview: false};
const settings = [...document.querySelectorAll("[data-setting]")];
const pendingSet = (result) => new Set((result?.trace?.pending_columns || []));
const pitchText = (pitch) => !pitch ? "—" : (pitch.pending ? "待核" : pitch.label);

async function api(path, options) {
  const response = await fetch("/staffframe" + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "请求失败，请检查输入后重试。");
  return body;
}

function route(tail) {
  return "/cards/" + state.card.id + tail;
}

function savedQuery() {
  return "?revision=" + state.card.revision;
}

function notice(message, target = "notice") {
  const element = get(target);
  element.textContent = message;
  element.hidden = !message;
}

async function action(work, target = "notice") {
  if (state.busy) return;
  state.busy = true;
  document.querySelectorAll("button, select").forEach((node) => { node.disabled = true; });
  notice("", target);
  try {
    await work();
  } catch (error) {
    notice(error.message, target);
  } finally {
    state.busy = false;
    document.querySelectorAll("button, select").forEach((node) => { node.disabled = false; });
  }
}

async function library() {
  const {cards} = await api("/cards");
  const select = get("library");
  select.replaceChildren(new Option("选取已存卡片", ""));
  for (const card of cards) select.append(new Option(card.name, card.id));
  select.value = state.card?.id || "";
}

function strokePolyline(ctx, points, color, width, dashed = false) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(dashed ? [7, 6] : []);
  ctx.beginPath();
  for (const [index, point] of points.entries()) {
    if (index === 0) ctx.moveTo(point[0], point[1]);
    else ctx.lineTo(point[0], point[1]);
  }
  ctx.stroke();
  ctx.setLineDash([]);
}

function paintSource() {
  const canvas = get("capture");
  const ctx = canvas.getContext("2d");
  const {photo, card, result} = state;
  if (!photo) return;
  canvas.width = card.width;
  canvas.height = card.height;
  ctx.drawImage(photo, 0, 0);
  const cfg = card.settings;
  const width = Math.max(1, card.width / 550);
  const trace = result.trace;
  if (result.staff_mode === "traced" && trace) {
    for (const [lineIndex, values] of trace.lines.entries()) {
      const points = values.map((y, offset) => [trace.left + offset, y]);
      strokePolyline(ctx, points, "#2e7d6b", width);
      for (const [start, end] of trace.pending_ranges[lineIndex]) {
        strokePolyline(ctx, points.slice(start - trace.left, end - trace.left + 1), "#d8942f", width + 0.6, true);
      }
    }
  } else {
    ctx.strokeStyle = "#ad4a3e";
    ctx.lineWidth = width;
    ctx.setLineDash([5, 6]);
    for (const y of result.lines_y) {
      ctx.beginPath();
      ctx.moveTo(cfg.left, y);
      ctx.lineTo(cfg.right, y);
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  const size = Math.max(8, card.width / 83);
  const drawn = result.reading;
  ctx.strokeStyle = pendingSet(result).has(drawn.x) ? "#d8942f" : "#23806a";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(drawn.x, drawn.y, size, 0, 2 * Math.PI);
  ctx.stroke();
  if (probe) {
    ctx.strokeStyle = "#47382c";
    ctx.strokeRect(probe.x - size / 2, probe.y - size / 2, size, size);
  }
}

function paintChart() {
  const canvas = get("chart");
  const ctx = canvas.getContext("2d");
  const {width, height} = canvas;
  const values = state.result.profile;
  const {card} = state;
  const xAt = (x) => 38 + (width - 61) * x / (card.height - 1);
  const yAt = (y) => height - 24 - (height - 38) * y / 255;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fffdf8";
  ctx.fillRect(0, 0, width, height);
  ctx.font = "12px monospace";
  ctx.fillStyle = "#70675d";
  ctx.strokeStyle = "#e2dacf";
  for (const level of [0, 128, 255]) {
    ctx.fillText(level, 5, yAt(level) + 3);
    ctx.beginPath();
    ctx.moveTo(xAt(0), yAt(level));
    ctx.lineTo(xAt(card.height - 1), yAt(level));
    ctx.stroke();
  }
  ctx.fillText("0", xAt(0), height - 6);
  ctx.fillText(String(card.height - 1), width - 36, height - 6);
  ctx.strokeStyle = "#9a382d";
  ctx.setLineDash([4, 6]);
  ctx.beginPath();
  ctx.moveTo(xAt(0), yAt(card.settings.ink_limit));
  ctx.lineTo(xAt(card.height - 1), yAt(card.settings.ink_limit));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.strokeStyle = "#2e6557";
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (const [index, row] of values.entries()) {
    if (index === 0) ctx.moveTo(xAt(row.y), yAt(row.level));
    else ctx.lineTo(xAt(row.y), yAt(row.level));
  }
  ctx.stroke();
  const read = state.result.reading;
  if (read.y >= values[0].y && read.y <= values.at(-1).y) {
    ctx.fillStyle = "#9a382d";
    ctx.beginPath();
    ctx.arc(xAt(read.y), yAt(read.level), 4, 0, 2 * Math.PI);
    ctx.fill();
  }
}

function pointLabel(point) {
  const pitch = point.pitch;
  if (!pitch) return "(" + point.x + ", " + point.y + ") · " + point.level.toFixed(1) + " · 超出校读范围";
  if (pitch.pending) return "(" + point.x + ", " + point.y + ") · " + point.level.toFixed(1) + " · 待核（长断段，不判音）";
  return "(" + point.x + ", " + point.y + ") · " + point.level.toFixed(1) + " · " + pitch.label;
}

function traceStatusText(result) {
  if (result.staff_mode !== "traced" || !result.trace_summary) return "";
  const summary = result.trace_summary;
  const ranges = summary.pending_ranges.flat();
  const rangeText = ranges.map(([start, end]) => start === end ? "X=" + start : "X " + start + "–" + end).join("，") || "无";
  return "已按真实弯线判音；续接短断墨 " + summary.bridged_columns.reduce((a, b) => a + b, 0) +
    " 列；待核 " + summary.pending_columns + " 列（" + rangeText + "），待核列不判音。";
}

function syncModeRadio(mode) {
  document.querySelectorAll("input[name=staff_mode]").forEach((radio) => {
    radio.checked = radio.value === mode;
  });
  refreshTraceStatus();
}

function selectedStaffMode() {
  return document.querySelector("input[name=staff_mode]:checked")?.value || "straight";
}

function refreshTraceStatus() {
  const status = get("trace-status");
  if (state.preview) {
    status.textContent = "预演（未保存）：" + traceStatusText(state.result);
    status.classList.toggle("pending", pendingSet(state.result).size > 0);
    return;
  }
  const mode = selectedStaffMode();
  if (mode !== "traced") {
    status.textContent = "";
    status.classList.remove("pending");
    return;
  }
  status.textContent = traceStatusText(state.result);
  status.classList.toggle("pending", pendingSet(state.result).size > 0);
}

function show() {
  const {card, result} = state;
  get("workspace").hidden = false;
  get("empty").hidden = true;
  get("origin").textContent = (card.synthetic ? "合成练习图" : "导入原片") + " · 修订 " + card.revision;
  get("card-title").textContent = card.name;
  get("dimensions").textContent = card.width + " × " + card.height + " px · " + card.photo_name;
  get("name").value = card.name;
  for (const field of settings) field.value = card.settings[field.dataset.setting];
  get("probe-x").max = card.width - 1;
  get("probe-y").max = card.height - 1;
  get("probe-x").value = state.probe?.x ?? result.reading.x;
  get("probe-y").value = state.probe?.y ?? result.reading.y;
  get("probe-result").textContent = state.probe ? pointLabel(state.probe) : "选择原片像素";
  const pitch = result.reading.pitch;
  get("pitch").textContent = pitchText(pitch);
  get("pitch").classList.toggle("review", Boolean(pitch?.pending));
  get("pitch-detail").textContent = !pitch
    ? "超出当前谱段的校读范围"
    : pitch.pending
      ? "该列五线中有长断段，需人工核对，不读音名"
      : "偏离最近线位 " + Math.abs(pitch.offset_px).toFixed(1) + " px";
  get("ink-verdict").textContent = result.reading.ink ? "墨迹" : "纸面";
  get("selected-level").textContent = "源像素 (" + result.reading.x + ", " + result.reading.y + ") · 亮度 " +
    result.reading.level.toFixed(1);
  for (const [id, filename] of Object.entries({proof: "proof.png", csv: "profile.csv", zip: "observation.zip"})) {
    get(id).href = "/staffframe" + route("/export/" + filename) + savedQuery();
  }
  syncModeRadio(card.staff_mode || "straight");
  paintSource();
  paintChart();
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/staffframe" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null, preview: false});
  get("library").value = card.id;
  show();
}

async function probe(x, y) {
  state.probe = await api(route("/probe") + savedQuery() + "&x=" + x + "&y=" + y);
  get("probe-x").value = x;
  get("probe-y").value = y;
  get("probe-result").textContent = pointLabel(state.probe);
  paintSource();
}

get("practice").addEventListener("click", () => action(async () => {
  await open(await api("/practice", {method: "POST"}));
  await library();
}));

get("library").addEventListener("change", () => {
  if (get("library").value) action(async () => open(await api("/cards/" + get("library").value)));
});
get("open-import").addEventListener("click", () => {
  notice("", "import-notice");
  get("import").showModal();
});
get("cancel-import").addEventListener("click", () => get("import").close());
get("upload").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const card = await api("/cards", {method: "POST", body: new FormData(event.target)});
    await open(card);
    await library();
    get("import").close();
    event.target.reset();
  }, "import-notice");
});
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
  state.mode = button.dataset.mode;
  document.querySelectorAll("[data-mode]").forEach((item) =>
    item.setAttribute("aria-pressed", String(item === button)));
}));
get("capture").addEventListener("click", (event) => {
  if (!state.card) return;
  const canvas = event.currentTarget;
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(canvas.width - 1, Math.floor((event.clientX - rect.left) * canvas.width / rect.width)));
  const y = Math.max(0, Math.min(canvas.height - 1, Math.floor((event.clientY - rect.top) * canvas.height / rect.height)));
  action(async () => {
    await probe(x, y);
    if (state.mode === "mark") {
      settings.find((field) => field.dataset.setting === "note_x").value = x;
      settings.find((field) => field.dataset.setting === "note_y").value = y;
      notice("已填入选音坐标，保存后更新写谱位置和校样。");
    }
  });
});
get("probe-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(() => probe(get("probe-x").value, get("probe-y").value));
});
get("calibration").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    const fields = Object.fromEntries(settings.map((field) => [field.dataset.setting, Number(field.value)]));
    const staffMode = document.querySelector("input[name=staff_mode]:checked")?.value || "straight";
    const card = await api(route(""), {
      method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({revision: state.card.revision, name: get("name").value,
                            settings: fields, staff_mode: staffMode}),
    });
    await open(card);
    await library();
    notice(staffMode === "traced"
      ? "已采用墨迹弯线判音并保存；重开仍是该方式，也可切回手填直线。"
      : "已按手填直线判音并保存。");
  });
});
get("trace-preview").addEventListener("click", () => action(async () => {
  const result = await api(route("/trace-preview") + savedQuery());
  state.result = result;
  state.preview = true;
  state.probe = null;
  show();
  notice("这是弯线预演，尚未保存；确认后选「墨迹跟线（弯线）」并保存。");
}));
document.querySelectorAll("input[name=staff_mode]").forEach((radio) => {
  radio.addEventListener("change", () => {
    if (state.preview) {
      state.preview = false;
      refreshTraceStatus();
    }
  });
});
settings.forEach((field) => field.addEventListener("input", () => {
  if (state.preview) {
    state.preview = false;
    refreshTraceStatus();
  }
}));
get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));
action(library);
