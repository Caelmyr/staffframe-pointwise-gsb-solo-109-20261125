"use strict";

const get = (id) => document.getElementById(id);
const state = {card: null, result: null, photo: null, probe: null, mode: "probe", busy: false};
const settings = [...document.querySelectorAll("[data-setting]")];

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

function paintSource() {
  const canvas = get("capture");
  const ctx = canvas.getContext("2d");
  const {photo, card, probe} = state;
  if (!photo) return;
  canvas.width = card.width;
  canvas.height = card.height;
  ctx.drawImage(photo, 0, 0);
  const cfg = card.settings;
  ctx.lineWidth = Math.max(1, card.width / 550);
  ctx.strokeStyle = "#ad4a3e";
  ctx.setLineDash([5, 6]);
  for (const y of state.result.lines_y) {
    ctx.beginPath();
    ctx.moveTo(cfg.left, y);
    ctx.lineTo(cfg.right, y);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  const size = Math.max(8, card.width / 83);
  const drawn = state.result.reading;
  ctx.strokeStyle = "#23806a";
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
  const pitch = point.pitch?.label || "超出校读范围";
  return "(" + point.x + ", " + point.y + ") · " + point.level.toFixed(1) + " · " + pitch;
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
  get("pitch").textContent = pitch?.label || "—";
  get("pitch-detail").textContent = pitch
    ? "偏离最近线位 " + Math.abs(pitch.offset_px).toFixed(1) + " px"
    : "超出当前谱段的校读范围";
  get("ink-verdict").textContent = result.reading.ink ? "墨迹" : "纸面";
  get("selected-level").textContent = "源像素 (" + result.reading.x + ", " + result.reading.y + ") · 亮度 " +
    result.reading.level.toFixed(1);
  for (const [id, filename] of Object.entries({
    proof: "proof.png", csv: "profile.csv", zip: "observation.zip", clean: "clean.png",
  })) {
    get(id).href = "/staffframe" + route("/export/" + filename) + savedQuery();
  }
  paintSource();
  paintChart();
}

async function open(card) {
  const base = "/cards/" + card.id;
  const photo = new Image();
  photo.src = "/staffframe" + base + "/capture.png";
  const [result] = await Promise.all([api(base + "/measurement?revision=" + card.revision), photo.decode()]);
  Object.assign(state, {card, result, photo, probe: null});
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
    const card = await api(route(""), {
      method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({revision: state.card.revision, name: get("name").value, settings: fields}),
    });
    await open(card);
    await library();
  });
});
get("prepare").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare") + savedQuery(), {method: "POST"});
  location.assign(url);
}));
get("prepare-clean").addEventListener("click", () => action(async () => {
  const {url} = await api(route("/prepare-clean") + savedQuery(), {method: "POST"});
  location.assign(url);
}));
action(library);
