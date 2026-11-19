"use strict";

const app = document.querySelector("#app");
const connection = document.querySelector("#connection");
const roomMark = document.querySelector("#top-room");
const notice = document.querySelector("#notice");
const icons = { holdem: "♠", sichuan: "和", kingdoms: "令" };
const gameLabels = { holdem: "德州扑克", sichuan: "川麻", kingdoms: "三国杀" };
const hostGreetings = {
  holdem: "坐吧。筹码摆稳了，再想怎么下注。",
  sichuan: "缺哪一门，想仔细些。来，坐。",
  kingdoms: "要争天下，先把座位坐稳。",
};
const gameDetails = {
  holdem: "无限注德州 · 2–8 人 · 5 手",
  sichuan: "血战到底 · 四人桌",
  kingdoms: "身份与阵营对局",
};
const holdemPhases = {
  preflop: "翻牌前", flop: "翻牌", turn: "转牌", river: "河牌", hand_end: "本手结算", finished: "散桌结算",
};
const kingdomsPhases = {
  bid: "竞叫地主", play: "出牌", dodge: "响应杀", rescue: "濒死救援", discard: "弃牌",
  counter: "响应锦囊", strike_response: "响应攻击", choice: "挑选五谷", finished: "结算",
  draft: "选将", select: "选将", select_general: "选将", lineup: "布阵",
  reserve: "选择备将", replace: "换将", replacement: "换将", judgement: "判定",
  fire_reveal: "展示火攻手牌", fire_pay: "选择弃牌", borrow: "响应借刀杀人", asset: "选择目标区域",
  weapon: "发动武器", follow_up: "选择追击", axe_cost: "支付贯石斧代价",
  draft_active: "选将上场", draft_nominate: "推荐备将", draft_reserve: "编排备将",
  reinforce_reserve: "备将接替", ct_commander: "选择主帅", ct_alliance: "选择势力",
  ct_compose: "编排军营", ct_trim: "调整上阵武将", ct_rotate: "走马换将", ct_skill: "武将技能", ct_skill_choice: "结算武将技能",
};
const suitNames = { C: "梅花", D: "方片", H: "红桃", S: "黑桃" };
const suitMarks = { C: "♣", D: "♦", H: "♥", S: "♠" };
const tileSuits = { m: "万", p: "筒", s: "条" };
const numerals = "〇一二三四五六七八九";
const pipPairs = (rows) => rows.flatMap((row) => [[0, row], [2, row]]);
const pips = {
  2: [[1, 0], [1, 4]],
  3: [[1, 0], [1, 2], [1, 4]],
  4: pipPairs([0, 4]),
  5: [...pipPairs([0, 4]), [1, 2]],
  6: pipPairs([0, 2, 4]),
  7: [...pipPairs([0, 2, 4]), [1, 1]],
  8: [...pipPairs([0, 2, 4]), [1, 1], [1, 3]],
  9: [...pipPairs([0, 1, 3, 4]), [1, 2]],
  10: [...pipPairs([0, 1, 3, 4]), [1, 1], [1, 3]],
};

let state = null;
let busy = false;
let polling = false;
let stopped = false;
let rulesOpen = false;
let savedName = "";
let savedRaise = null;
let openedCard = "";
let packDraft = null;
const openTargets = new Set();
let noticeTimer;

function el(tag, className = "", ...children) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  item.append(...children.filter((child) => child !== null && child !== undefined));
  return item;
}

function panel(title, badge = null) {
  return el("section", "panel", el("div", "panel-head", el("h2", "", title), badge));
}

function setConnection(status, label) {
  connection.dataset.status = status;
  connection.textContent = label;
}

function alertMessage(message, error = false) {
  clearTimeout(noticeTimer);
  notice.textContent = message;
  notice.dataset.error = String(error);
  notice.hidden = false;
  noticeTimer = setTimeout(() => { notice.hidden = true; }, 4600);
}

function playerName(id) {
  return state?.players.find((player) => player.player_id === id)?.name || "玩家";
}

function namesIn(text) {
  return String(text || "").replace(/\bp[0-9a-f]{16}\b/g, playerName);
}

function count(value) {
  return Number(value).toLocaleString("zh-CN");
}

function phaseName(game) {
  if (state.kind === "holdem") return holdemPhases[game.phase] || "行牌";
  if (state.kind === "sichuan") return game.phase_label || "行牌";
  return game.phase_label || kingdomsPhases[game.phase] || "等待操作";
}

function currentMode() {
  return state.modes?.find((mode) => mode.id === state.mode);
}

function exactSeats(settings) {
  if (Array.isArray(settings.exact)) return settings.exact;
  if (Number.isInteger(settings.exact)) return [settings.exact];
  if (settings.exact || settings.minimum === settings.capacity) return [settings.capacity];
  return null;
}

function seatRange(settings) {
  const exact = exactSeats(settings);
  return exact ? `${exact.join(" 或 ")} 人` : `${settings.minimum}–${settings.capacity} 人`;
}

function readySeats(count, settings) {
  const exact = exactSeats(settings);
  return exact ? exact.includes(count) : count >= settings.minimum && count <= settings.capacity;
}

function subtitle(game = null) {
  const mode = currentMode();
  return mode
    ? `${game?.mode_name || mode.name} · ${seatRange(mode)}`
    : gameDetails[state.kind];
}

function lobbyBanner(players) {
  const heading = el("h1", "", gameLabels[state.kind] || state.settings.name);
  heading.id = "scene-title";
  const art = el("div", "scene-art");
  art.setAttribute("aria-hidden", "true");
  const greeting = el("blockquote", "host-greeting",
    el("p", "", hostGreetings[state.kind]), el("cite", "", "祀"));
  const banner = el("section", "scene", art, el("div", "scene-inner",
    el("span", "scene-sigil", icons[state.kind]), heading, el("p", "scene-rule", subtitle()),
    greeting,
    el("div", "scene-status",
      el("span", "occupancy", `${players.length} / ${state.settings.capacity} 席`),
      state.demo ? el("span", "mode-indicator", "本地试玩") : null)));
  banner.setAttribute("aria-labelledby", heading.id);
  return banner;
}

function matchHeader(game) {
  const title = el("div", "match-title", el("span", "scene-sigil", icons[state.kind]),
    el("div", "", el("h1", "", gameLabels[state.kind] || state.settings.name), el("p", "", subtitle(game))));
  const ruleLink = el("a", "button subtle compact", "规则");
  ruleLink.href = "#room-rules";
  ruleLink.addEventListener("click", () => {
    const details = document.querySelector("#room-rules");
    if (details) details.open = true;
  });
  return el("header", "match-head", title, el("div", "match-tools", ruleLink,
    state.host && state.phase === "playing" ? makeButton("结束本桌", closeTable, "subtle compact") : null));
}

function makeButton(label, onClick, classes = "", disabled = false) {
  const button = el("button", ["button", classes].filter(Boolean).join(" "), label);
  button.type = "button";
  button.disabled = disabled;
  button.addEventListener("click", onClick);
  return button;
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 7500);
  try {
    const response = await fetch(path, {
      cache: "no-store", credentials: "same-origin", signal: controller.signal, ...options,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok || !data) {
      const error = new Error(data?.error || "暂时连不上牌桌，请稍后再试。");
      error.status = response.status;
      error.roomError = data?.room_error === true;
      throw error;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

function accept(next) {
  if (state && next.revision <= state.revision) return;
  if (state && state.seat !== "visitor" && next.seat === "visitor") {
    alertMessage("已离开座位，可以重新申请入座。");
  }
  if (state && !state.host && next.host) {
    alertMessage("你已接任桌主，可以管理本桌。");
  }
  state = next;
  render();
}

async function refresh() {
  if (stopped || polling || busy) return;
  polling = true;
  try {
    accept(await request("state"));
    setConnection("online", "已连接");
  } catch (error) {
    if (error.roomError && (error.status === 403 || error.status === 404)) {
      stopped = true;
      setConnection("offline", "邀请失效");
      app.replaceChildren(el("section", "panel initial", el("h1", "", "请从群聊进入"), el("p", "", error.message)));
    } else {
      setConnection("waiting", "正在重连");
      if (!state) {
        app.replaceChildren(el("section", "panel initial", el("h1", "", "正在重新连接牌桌"),
          el("p", "", "网络暂时不稳定，正在自动重试。"), makeButton("立即重试", refresh, "primary")));
      }
    }
  } finally {
    polling = false;
  }
}

async function submit(action, value = null) {
  if (busy || !state || stopped) return false;
  busy = true;
  app.setAttribute("aria-busy", "true");
  try {
    const next = await request("action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, value, revision: state.revision }),
    });
    accept(next);
    setConnection("online", "已连接");
    return true;
  } catch (error) {
    alertMessage(error.name === "AbortError" ? "正在确认这一步，请稍后再操作。" : error.message, true);
    if (error.status === 409) {
      try {
        accept(await request("state"));
      } catch {
        setConnection("waiting", "正在重连");
      }
    }
    return false;
  } finally {
    busy = false;
    app.removeAttribute("aria-busy");
    refresh();
  }
}

function playingCard(code, className = "") {
  if (!code) {
    const empty = el("div", `playing-card empty ${className}`, el("span", "", "◆"));
    empty.setAttribute("aria-label", "待翻牌");
    return empty;
  }
  const rank = code[0] === "T" ? "10" : code[0];
  const suit = code[1];
  const symbol = suitMarks[suit];
  const variant = suit === "H" || suit === "D" ? "red" : "";
  const corner = (bottom) => el(
    "span", `card-corner${bottom ? " card-corner-bottom" : ""}`, rank, el("em", "", symbol),
  );
  const body = pips[rank]
    ? el("span", "suit-grid", ...pips[rank].map(([x, y]) => el("span", `p-x${x} p-y${y}`, symbol)))
    : el("span", "court-symbol", symbol);
  const card = el("div", `playing-card ${variant} ${className}`, corner(false), body, corner(true));
  card.setAttribute("role", "img");
  card.setAttribute("aria-label", `${suitNames[suit]} ${rank}`);
  return card;
}

function tileLabel(value) {
  return value ? `${numerals[Number(value[1])]}${tileSuits[value[0]]}` : "背面牌";
}

function tileCard(value, className = "", option = null) {
  const type = value ? value[0] : "back";
  const rank = value ? Number(value[1]) : 0;
  const marks = type === "p" || type === "s";
  const positions = rank === 1 ? [[1, 2]] : pips[rank] || [];
  const face = marks ? el("span", "tile-pattern", ...positions.map(([x, y], index) =>
    el("i", `p-x${x} p-y${y}${index === 0 ? " spot" : ""}`))) : null;
  const tile = el(
    option ? "button" : "span", `tile ${type} ${className}`,
    face, el("strong", marks ? "tile-rank" : "", value ? numerals[rank] : "◆"),
    value ? el("small", "", tileSuits[type]) : null,
  );
  tile.setAttribute("aria-label", option?.label || tileLabel(value));
  if (option) {
    tile.type = "button";
    tile.title = option.label;
    tile.addEventListener("click", () => submit(option.id, option.value));
  }
  return tile;
}

function seatLine(seat, index, game) {
  if (!seat) {
    return el("div", "seat vacant", el("span", "avatar", "+"),
      el("div", "seat-description", el("strong", "seat-name", "空位"), el("span", "seat-info", "待入座")));
  }
  const player = state.players.find((entry) => entry.player_id === seat.id);
  const name = player?.name || "玩家";
  let info = player?.host ? "桌主" : "已入座";
  let marker = "";
  let bet = null;
  if (game && state.kind === "holdem") {
    info = el("span", "", count(seat.stack), el("span", "seat-unit", " 筹码"));
    if (seat.status === "folded") info = "已弃牌";
    if (seat.status === "all_in") info = "已全下";
    if (seat.status === "out") info = "筹码用尽";
    if (seat.bet) bet = el("span", "seat-bet", `下注 ${count(seat.bet)}`);
    if (seat.id === game.button) marker = "D";
  } else if (game && state.kind === "sichuan") {
    info = seat.won ? `已胡 · ${seat.points} 分` : `${seat.points} 分 · ${seat.hand_count} 张`;
    if (seat.missing && !seat.won) info += ` · 缺${tileSuits[seat.missing]}`;
    if (seat.id === game.dealer) marker = "庄";
  } else if (game) {
    info = seat.alive ? (seat.max_hp ? `♥ ${seat.hp}/${seat.max_hp} · ${seat.hand_count} 张` : "待选将") : "已出局";
    marker = [...new Set([seat.team, seat.role].filter(Boolean))].join(" · ");
  }
  const classes = ["seat"];
  if (seat.id === game?.turn) classes.push("current");
  if (seat.id === state.mine) classes.push("my-seat");
  if (seat.status === "out" || seat.alive === false) classes.push("out");
  const avatar = el("span", `avatar tone-${index % 4}`, Array.from(name)[0]);
  avatar.setAttribute("aria-hidden", "true");
  const generals = game && state.kind === "kingdoms" ? (seat.generals || [seat.general].filter(Boolean)) : [];
  const element = el("div", classes.join(" "), avatar,
    el("div", "seat-description", el("strong", "seat-name", name, seat.id === state.mine ? el("span", "me", "自己") : null), el("span", "seat-info", info),
      generals.length ? el("span", "seat-general", generals.map((general) => general.name).join(" · ")) : null, bet),
    marker ? el("span", "seat-marker", marker) : null,
    game && state.kind === "kingdoms" ? publicGear(seat) : null);
  if (state.kind === "kingdoms" && seat.team) element.dataset.team = seat.team;
  return element;
}

function publicGear(seat) {
  const equipment = (seat.equipment || []).map((item) => item.name);
  const judgement = (seat.judgement || []).map((item) => typeof item === "string" ? item : item.name);
  const lines = [...equipment, ...judgement].filter(Boolean);
  return lines.length ? el("span", "seat-gear", lines.join(" · ")) : null;
}

function tableBody(game) {
  if (game?.finished) {
    return el("div", "table-center table-finale", el("span", "lobby-mark", "结"),
      el("h2", "", "整桌结算"), el("p", "", namesIn(game.result)));
  }
  if (!game) {
    return el("div", "table-center empty-table", el("span", "lobby-mark", icons[state.kind]),
      el("h2", "", state.phase === "closed" ? "牌桌已关闭" : "等待发牌"),
      el("p", "", state.phase === "closed" ? "回原群可以开新桌。" : `${seatRange(currentMode() || state.settings)}开局`));
  }
  if (state.kind === "holdem") {
    const rack = el("div", "rack", ...Array.from({ length: 5 }, (_, index) => playingCard(game.board[index])));
    rack.setAttribute("aria-label", "公共牌");
    const chips = game.lastResult && ["hand_end", "finished"].includes(game.phase) ? game.lastResult.pot : game.pot;
    return el("div", "table-center",
      el("div", "center-stat", el("span", "pot-icon"), "底池", el("strong", "", count(chips))),
      rack, el("p", "", `第 ${game.hand} / ${game.maxHands} 手 · 盲注 ${game.blinds.small} / ${game.blinds.big}`));
  }
  if (state.kind === "sichuan") {
    const latest = game.last_tile;
    return el("div", "table-center", el("h2", "", phaseName(game)),
      latest ? el("div", "center-tiles", tileCard(latest.tile), el("p", "center-caption",
        `${playerName(latest.from)} ${latest.kind === "added_kong" ? "补杠" : "打出"}`)) : null,
      el("p", "", `牌墙 ${game.wall_count} 张${game.waiting ? ` · ${game.waiting} 人待响应` : ""}`),
      latest ? null : el("p", "", game.last_event));
  }
  const event = game.event;
  const tournament = game.tournament;
  if (tournament?.score) {
    const scoreboard = el("div", "tournament-score",
      ...["暖方", "冷方"].map((team) => el("div", team === "暖方" ? "warm" : "cool",
        el("span", "", team), el("strong", "", tournament.score[team]),
        el("small", "", `备将 ${tournament.reserve_counts[team]}`))));
    const context = tournament.reinforce_target ? `${playerName(tournament.reinforce_target)} 等待接替`
      : event?.actor && event.target ? `${playerName(event.actor)} → ${playerName(event.target)}` : "";
    return el("div", "table-center", el("h2", "", event?.card || phaseName(game)), scoreboard,
      context ? el("p", "", context) : null);
  }
  const stateName = event?.card || phaseName(game);
  const actionContext = event?.actor && event.target
    ? `${playerName(event.actor)} → ${playerName(event.target)}`
    : game.turn_number > 0 ? `第 ${game.turn_number} 回合 · 牌堆 ${game.deck_count} 张` : "";
  return el("div", "table-center", el("span", "lobby-mark", "令"), el("h2", "", stateName),
    actionContext ? el("p", "", actionContext) : null,
    event?.cards?.length ? el("div", "table-choices", ...event.cards.map((card) =>
      el("span", "", typeof card === "string" ? card : card.name))) : null);
}

const seatAnchors = {
  2: ["n", "s"],
  3: ["nw", "ne", "s"],
  4: ["nw", "ne", "se", "sw"],
  5: ["nw", "n", "ne", "se", "sw"],
  6: ["nw", "n", "ne", "se", "s", "sw"],
  7: ["nw", "n", "ne", "e", "se", "s", "sw"],
  8: ["nw", "n", "ne", "e", "se", "s", "sw", "w"],
};

function table(game, players) {
  const occupied = game ? (game.seats || game.players) : players.map((player) => ({ id: player.player_id }));
  const capacity = game ? occupied.length : state.settings.capacity;
  const anchors = seatAnchors[capacity] || seatAnchors[8];
  const ownIndex = occupied.findIndex((seat) => seat.id === state.mine);
  const nearAnchor = anchors.findIndex((anchor) => anchor === "s" || anchor === "sw");
  const rotation = ownIndex < 0 || nearAnchor < 0 ? 0 : ownIndex - nearAnchor;
  const children = Array.from({ length: capacity }, (_, index) => {
    const seatIndex = (index + rotation + capacity) % capacity;
    const seat = seatLine(occupied[seatIndex], seatIndex, game);
    seat.classList.add(`anchor-${anchors[index]}`);
    return seat;
  });
  const half = Math.ceil(capacity / 2);
  const divider = `seat-row seats-${half}`;
  const board = el("section", `table table-${state.kind}`,
    el("div", "table-bar", el("div", "table-label", el("span", "table-glyph", icons[state.kind]),
      el("strong", "", game ? phaseName(game) : "开桌大厅")),
    el("span", "table-count", `${occupied.length} 席`)),
    el("div", "arena", el("div", "felt-orbit"), 
      el("div", `${divider} top-lane`, ...children.slice(0, half)), tableBody(game),
      el("div", `${divider} bottom-lane`, ...children.slice(half))),
    game && !game.finished ? turnStrip(game) : null);
  board.dataset.capacity = capacity;
  board.setAttribute("aria-label", "牌桌");
  return board;
}

function turnStrip(game) {
  const actionable = state.seat === "seated" && game.actions.length > 0;
  const message = actionable ? "到你操作" : game.turn ? `等待 ${playerName(game.turn)}` : "等待其他玩家";
  if (!actionable) return el("div", "turn-strip", el("span", "", message));
  const link = el("a", "turn-strip yours", el("span", "", message));
  link.href = "#hand-controls";
  return link;
}

function modePicker(players) {
  if (state.kind !== "kingdoms" || !state.modes?.length) return null;
  const selected = currentMode();
  if (!state.host) {
    if (!selected?.description) return null;
    return el("p", "mode-description", selected.description);
  }
  const list = el("div", "mode-list");
  list.setAttribute("aria-label", "三国杀玩法");
  for (const mode of state.modes) {
    const picked = mode.id === state.mode;
    const unavailable = players.length > mode.capacity;
    const button = el("button", "mode-option", el("strong", "", mode.name), el("span", "", seatRange(mode)));
    button.type = "button";
    button.disabled = unavailable;
    button.setAttribute("aria-pressed", String(picked));
    button.addEventListener("click", () => { if (!picked) submit("mode", mode.id); });
    if (unavailable) button.title = "已入座人数超过该玩法上限";
    list.append(button);
  }
  return el("div", "mode-picker", el("h3", "", "本桌玩法"), list,
    selected?.description ? el("p", "mode-description", selected.description) : null);
}

function packPicker() {
  if (state.kind !== "kingdoms" || !state.host || !state.packs?.length) return null;
  const current = state.enabled_packs || [];
  const selected = new Set(packDraft ?? current);
  const wrapper = el("fieldset", "pack-picker", el("legend", "", "武将扩展"));
  const list = el("div", "pack-list");
  const save = makeButton("应用将包", async () => {
    if (await submit("packs", Array.from(selected))) packDraft = null;
  }, "subtle compact", true);
  for (const pack of state.packs) {
    const input = el("input");
    input.type = "checkbox";
    input.checked = selected.has(pack.id);
    input.addEventListener("change", () => {
      if (input.checked) selected.add(pack.id);
      else selected.delete(pack.id);
      packDraft = [...selected];
      save.disabled = selected.size === current.length && current.every((item) => selected.has(item));
    });
    list.append(el("label", "pack-option", input, el("span", "", pack.name)));
  }
  wrapper.append(list, save);
  save.disabled = selected.size === current.length && current.every((item) => selected.has(item));
  return wrapper;
}

function lobbyControls(players) {
  if (state.phase === "closed") {
    const box = panel("牌桌已关闭");
    box.append(el("p", "", "桌主已关闭这张牌桌。"));
    return box;
  }
  if (state.seat === "pending") {
    const box = panel("等待入座", el("span", "chip", "申请已送达"));
    box.append(el("p", "", "桌主确认后，你的座位会显示在牌桌上。"),
      makeButton("取消申请", () => submit("leave"), "subtle"));
    return box;
  }
  if (state.seat === "seated") {
    const box = panel(state.host ? "桌主操作" : "等待开局", el("span", "chip", "已入座"));
    if (!state.host) {
      box.append(el("p", "", "桌主发牌后，手牌会出现在这里。"),
        makeButton("离开大厅", () => submit("leave"), "subtle"));
      return box;
    }
    const settings = currentMode() || state.settings;
    const ready = readySeats(players.length, settings);
    const picker = modePicker(players);
    if (picker) box.append(picker);
    const packs = packPicker();
    if (packs) box.append(packs);
    box.append(
      el("p", "", ready ? "人数已齐，可以发牌。" : `本桌需要 ${seatRange(settings)}，当前 ${players.length} 人入座。`),
      el("div", "action-buttons", makeButton("发牌开局", () => submit("start"), "primary", !ready),
        makeButton("离开大厅", () => submit("leave"), "subtle"), makeButton("关闭牌桌", closeTable, "subtle")));
    return box;
  }
  const hostExists = players.some((player) => player.host);
  const box = panel(hostExists ? "申请入座" : "成为桌主");
  const input = el("input", "field");
  input.type = "text";
  input.placeholder = "输入牌桌昵称";
  input.autocomplete = "nickname";
  input.dataset.actionField = "nickname";
  input.maxLength = 16;
  input.required = true;
  input.value = savedName;
  input.setAttribute("aria-label", "牌桌昵称");
  input.addEventListener("input", () => { savedName = input.value; });
  const button = el("button", "button primary", hostExists ? "申请入座" : "入座并开桌");
  button.type = "submit";
  const form = el("form", "join-form", input, button);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit("join", input.value.trim());
  });
  box.append(form);
  return box;
}

function closeTable() {
  const message = state.phase === "playing" ? "确定提前结束本桌？本次牌局将关闭。" : "要关闭这张牌桌吗？";
  if (window.confirm(message)) submit("close");
}

function applications() {
  if (!state.host || state.phase !== "lobby") return null;
  const pending = state.players.filter((player) => player.seat === "pending");
  const box = panel("入座申请", el("span", "chip", String(pending.length)));
  if (!pending.length) {
    box.append(el("p", "", "群友点邀请链接后，可在这里确认入座。"));
  } else {
    box.append(el("ul", "application-list", ...pending.map((player) =>
      el("li", "application", el("strong", "", player.name),
        el("div", "action-buttons", makeButton("同意", () => submit("approve", player.player_id), "primary compact"),
          makeButton("拒绝", () => submit("decline", player.player_id), "compact subtle"))))));
  }
  return box;
}

function actionButton(option) {
  const suffix = option.target ? ` · ${playerName(option.target)}` : "";
  const kind = ["fold", "all_in", "take_damage"].includes(option.id) ? "danger" : "";
  const primary = ["call", "check", "next", "exchange_submit", "self_win", "win", "dodge", "rescue"].includes(option.id);
  return makeButton(option.label + suffix, () => submit(option.id, option.value), primary ? "primary" : kind);
}

function cardStamp(card) {
  const suit = { 黑桃: "♠", 红桃: "♥", 方片: "♦", 梅花: "♣", S: "♠", H: "♥", D: "♦", C: "♣" }[card.suit];
  const rank = { 1: "A", 11: "J", 12: "Q", 13: "K" }[card.rank] ?? card.rank;
  return suit && rank ? el("span", `war-stamp${suit === "♥" || suit === "♦" ? " red" : ""}`, rank, suit) : null;
}

function kingdomActions(actions) {
  const direct = [];
  const groups = new Map();
  for (const option of actions) {
    if (option.target && ["attack", "trick", "aid"].includes(option.id)) {
      const key = option.id + ":" + option.label;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(option);
    } else {
      direct.push(option);
    }
  }
  const selection = el("div", "target-options");
  for (const [key, options] of groups) {
    if (options.length < 2) {
      direct.unshift(options[0]);
      continue;
    }
    const group = el("details", "target-group", el("summary", "", options[0].label),
      el("div", "target-buttons", ...options.map((option) =>
        makeButton(playerName(option.target), () => submit(option.id, option.value), "compact"))));
    group.open = openTargets.has(key);
    group.addEventListener("toggle", () => {
      if (!group.isConnected) return;
      if (group.open) openTargets.add(key);
      else openTargets.delete(key);
    });
    selection.append(group);
  }
  return el("div", "kingdom-actions", selection.childElementCount ? selection : null,
    direct.length ? el("div", "action-buttons", ...direct.map(actionButton)) : null);
}

function raiseForm(option, game) {
  const marker = [game.hand, game.phase, game.turn, option.min, option.max].join("|");
  const input = el("input", "field");
  input.type = "number";
  input.dataset.actionField = "raise";
  input.inputMode = "numeric";
  input.min = option.min;
  input.max = option.max;
  input.step = option.step || 1;
  input.required = true;
  input.value = savedRaise?.marker === marker ? savedRaise.value : option.min;
  input.addEventListener("input", () => { savedRaise = { marker, value: input.value }; });
  const label = el("label", "", "本轮下注总额", input);
  const button = el("button", "button primary", option.label);
  button.type = "submit";
  const form = el("form", "raise-form", label, button,
    el("small", "", `可输入 ${count(option.min)}–${count(option.max)} 筹码`));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.validity.valid) submit(option.id, input.valueAsNumber);
  });
  return form;
}

function mahjongRack(game) {
  const countSelected = new Map();
  for (const tile of game.exchange_draft) {
    countSelected.set(tile, (countSelected.get(tile) || 0) + 1);
  }
  const seen = new Map();
  return el("div", "rack", ...game.my_hand.map((value) => {
    const seenCount = (seen.get(value) || 0) + 1;
    seen.set(value, seenCount);
    const selected = (countSelected.get(value) || 0) >= seenCount;
    const id = game.phase === "exchange" ? (selected ? "exchange_remove" : "exchange_add") : "discard";
    const option = game.actions.find((action) => action.id === id && action.value === value);
    return tileCard(value, `rack-tile${selected ? " selected" : ""}${option && !selected ? " hot" : ""}`, option);
  }));
}

function kingdomRack(game) {
  const details = el("div", "card-guide");
  details.id = "inspect-card";
  const list = el("div", "rack war-rack");
  list.setAttribute("aria-label", "我的手牌");
  function highlight() {
    const card = game.hand.find((entry) => entry.id === openedCard);
    details.hidden = !card;
    if (card) {
      const usable = game.actions.filter((option) => option.card === card.id);
      details.replaceChildren(el("strong", "", card.name), el("p", "", card.description),
        ...[usable.length ? kingdomActions(usable) : null].filter(Boolean));
    }
    for (const item of list.children) item.setAttribute("aria-expanded", String(item.dataset.card === openedCard));
  }
  for (const card of game.hand) {
    const type = card.type || "";
    const symbol = { basic: "令", equipment: "装", trick: "策" }[type] || "◇";
    const art = { strike: "slash", fire_strike: "fire", thunder_strike: "thunder", dodge: "dodge", peach: "peach" }[card.kind]
      || (type === "trick" ? "stratagem" : "");
    const usable = game.actions.some((option) => option.card === card.id);
    const button = el("button", `war-card ${type}${usable ? " playable" : ""}`,
      art ? el("span", `war-illustration ${art}`) : el("span", "war-glyph", symbol), cardStamp(card), el("strong", "", card.name),
      card.count > 1 ? el("small", "war-count", `×${card.count}`) : null);
    button.type = "button";
    button.dataset.card = card.id;
    button.setAttribute("aria-label", `查看${card.name}${card.suit ? `，${card.suit}${card.rank}` : ""}，手中有 ${card.count} 张`);
    button.setAttribute("aria-controls", details.id);
    button.addEventListener("click", () => {
      openedCard = openedCard === card.id ? "" : card.id;
      highlight();
    });
    list.append(button);
  }
  highlight();
  return el("div", "", list, details);
}

function generalRail(generals) {
  return el("div", "general-rail", ...generals.map((general) => {
    const printing = packName(general);
    const card = el("div", "general-card", el("span", "general-seal", general.faction || "将"),
      el("div", "general-copy", el("strong", "", general.name, printing === "标准" ? null : el("small", "printing", printing)),
        general.locked ? el("small", "unimplemented-skills", "乐不思蜀 · 技能锁定") : null,
        general.description || general.skill ? el("p", "", general.description || general.skill) : null,
        general.omitted?.length ? el("small", "unimplemented-skills", `待补 · ${general.omitted.join("、")}`) : null));
    if (general.faction) card.dataset.faction = general.faction;
    return card;
  }));
}

const draftActions = new Set(["select_active", "nominate_general", "select_reserve", "revive_general", "select_commander",
  "trim_general"]);
const rosterActions = new Set(["add_general", "remove_general"]);

function packName(general) {
  if (general.pack === "chengtian") return general.code || general.id;
  const pack = general.pack || general.id?.split("_", 1)[0];
  return state.packs?.find((item) => item.id === pack)?.name || "标准";
}

function draftOptions(game) {
  const choices = game.actions.filter((option) => draftActions.has(option.id) && option.general);
  if (!choices.length) return null;
  return el("div", "draft-grid", ...choices.map((option) => {
    const general = option.general;
    const choose = actionButton({ ...option, label: option.label.split(" ")[0] });
    choose.setAttribute("aria-label", option.label);
    const card = el("article", "draft-card",
      el("div", "draft-letter", general.faction || "将"),
      el("h3", "", general.name, el("small", "printing", packName(general))),
      general.hp ? el("span", "draft-hp", `♥ ${general.hp}`) : null,
      general.description ? el("p", "", general.description) : null, choose);
    if (general.faction) card.dataset.faction = general.faction;
    return card;
  }));
}

function rosterBuilder(tournament, actions) {
  const current = tournament?.your_roster || [];
  if (!current.length) return null;
  const selection = new Map(current.map((item) => [item.id, item]));
  const additions = new Map();
  const removals = new Map();
  for (const option of actions) {
    if (option.id === "add_general") {
      additions.set(option.value, option);
      if (!selection.has(option.value) && option.general) selection.set(option.value, option.general);
    } else if (option.id === "remove_general") {
      removals.set(option.value, option);
    }
  }
  const list = el("ul", "roster-editor");
  for (const [key, general] of selection) {
    const plus = additions.get(key);
    const minus = removals.get(key);
    const add = makeButton("+", () => submit(plus.id, plus.value), "compact", !plus);
    const remove = makeButton("−", () => submit(minus.id, minus.value), "compact subtle", !minus);
    add.setAttribute("aria-label", `收录${general.name}`);
    remove.setAttribute("aria-label", `移出${general.name}`);
    list.append(el("li", "", el("strong", "", general.name, el("small", "printing", packName(general))),
      el("span", "", general.faction),
      el("div", "roster-count", remove, el("span", "", general.count || 0), add)));
  }
  const quantity = current.reduce((sum, card) => sum + card.count, 0);
  return el("div", "army-builder",
    el("div", "builder-head", el("h3", "", "军营武将"), el("span", "chip", `${quantity} / 40 张`)),
    list);
}

function tournamentNotes(game) {
  const tournament = game.tournament;
  if (!tournament) return null;
  const info = [
    tournament.your_active ? el("div", "", el("span", "", "本席上场"), tournament.your_active.name) : null,
    tournament.your_nominees?.length ? el("div", "", el("span", "", "已推荐"), tournament.your_nominees.map((g) => g.name).join("、")) : null,
    tournament.your_reserves?.length ? el("div", "", el("span", "", "本队备将"), tournament.your_reserves.map((g) => g.name).join("、")) : null,
    tournament.your_commander ? el("div", "", el("span", "", "主帅"), tournament.your_commander.name) : null,
    tournament.your_alliance ? el("div", "", el("span", "", "势力"), tournament.your_alliance === "single" ? "单势力" : `联用${tournament.your_alliance}`) : null,
  ].filter(Boolean);
  return info.length ? el("div", "tournament-notes", ...info) : null;
}

function armies(game) {
  const rows = game.tournament?.armies;
  if (!rows?.some((row) => row.count || row.retired || row.field.length)) return null;
  const box = panel("双方军营");
  box.append(el("div", "army-overview", ...rows.map((row) =>
    el("article", "",
      el("div", "builder-head", el("strong", "", playerName(row.player)),
        el("span", "chip", `待上阵 ${row.count}`)),
      row.field.length ? generalRail(row.field) : null,
      row.retired ? el("p", "", `休整区 ${row.retired}`) : null))));
  return box;
}

function handControls(game) {
  const seated = state.seat === "seated";
  const ownTurn = game.actions.length > 0;
  const label = /^(?:ct_|draft_|reinforce_)/.test(game.phase) && !game.hand?.length ? phaseName(game) : "我的手牌";
  const box = panel(seated ? label : "观战",
    el("span", `chip${ownTurn ? " coral" : ""}`, ownTurn ? "可操作" : game.finished ? "已结束" : "等待中"));
  box.id = "hand-controls";
  box.classList.add("hand-deck");
  box.dataset.active = String(ownTurn);
  if (!seated) {
    box.append(el("p", "", "可以查看牌桌进度和公开信息。"));
    return box;
  }
  let actionPanel = game.actions;
  if (state.kind === "holdem") {
    const own = game.players.find((seat) => seat.id === state.mine);
    const rack = el("div", "rack", ...own.cards.map((code) => playingCard(code, "hand-card")));
    rack.append(el("span", "chip", `${count(own.stack)} 筹码`));
    box.append(rack);
  } else if (state.kind === "sichuan") {
    box.append(mahjongRack(game));
    if (game.phase === "exchange") {
      box.append(el("p", "hand-foot", game.actions.length
        ? `点选同一花色的三张牌 · 已选 ${game.exchange_draft.length} / 3` : "已提交换牌，等待另外三家。"));
    }
    actionPanel = actionPanel.filter((option) => !["exchange_add", "exchange_remove", "discard"].includes(option.id));
  } else {
    const identity = [...new Set([game.your_team, game.your_role].filter(Boolean))].join(" · ");
    if (identity) box.append(el("p", "panel-header-copy", el("span", "", "身份"), identity));
    const generals = game.your_generals || [game.your_general].filter(Boolean);
    if (generals.length) box.append(generalRail(generals));
    const notes = tournamentNotes(game);
    if (notes) box.append(notes);
    const draft = draftOptions(game);
    if (draft) {
      box.append(draft);
      actionPanel = actionPanel.filter((option) => !(draftActions.has(option.id) && option.general));
    }
    if (game.phase === "ct_compose") {
      const roster = rosterBuilder(game.tournament, game.actions);
      if (roster) {
        box.append(roster);
        actionPanel = actionPanel.filter((option) => !rosterActions.has(option.id));
      }
    }
    if (game.hand?.length) box.append(kingdomRack(game));
    else if (!draft && game.phase === "play") box.append(el("p", "", "手牌已用完。"));
  }
  if (!ownTurn && !game.finished && game.turn) {
    box.append(el("p", "hand-foot", `等待 ${playerName(game.turn)} 操作`));
  }
  const direct = actionPanel.filter((option) => typeof option.min !== "number");
  const actionBar = el("div", "action-bar");
  if (direct.length) actionBar.append(state.kind === "kingdoms"
    ? kingdomActions(direct) : el("div", "action-buttons", ...direct.map(actionButton)));
  for (const option of actionPanel.filter((action) => typeof action.min === "number")) {
    actionBar.append(raiseForm(option, game));
  }
  if (actionBar.childElementCount) box.append(actionBar);
  return box;
}

function summary(game) {
  if (!game?.finished) return null;
  const box = panel("整桌结算", el("span", "chip", "已结束"));
  box.append(el("p", "text-summary", namesIn(game.result)));
  if (state.kind === "holdem") {
    box.append(el("hr", "soft-rule"), rankings(game.rankings, "stack", " 筹码"));
  } else if (state.kind === "sichuan") {
    const scores = [...game.seats].sort((a, b) => b.points - a.points);
    box.append(el("hr", "soft-rule"), rankings(scores, "points", " 分"));
  } else if (game.winners?.length) {
    box.append(el("p", "", game.winners.map(playerName).join("、")));
  }
  return box;
}

function rankings(rows, tally, unit) {
  return el("ol", "rankings", ...rows.map((row) => el("li", "ranking",
    el("strong", "label", playerName(row.id)), el("span", "score", count(row[tally]) + unit))));
}

function tableUpdates(game) {
  if (state.kind === "holdem") {
    const previous = game.lastResult;
    if (!previous) return null;
    const box = panel("最近一手", el("span", "chip", `${count(previous.pot)} 底池`));
    const answer = el("ul", "rankings");
    for (const payout of previous.payouts) {
      answer.append(el("li", "ranking", el("strong", "label", playerName(payout.id) + (payout.hand ? ` · ${payout.hand}` : "")),
        el("span", "score", `+${count(payout.amount)}`)));
    }
    for (const refund of previous.refunds) {
      answer.append(el("li", "ranking", el("span", "label", playerName(refund.id) + " · 退回"),
        el("span", "score", count(refund.amount))));
    }
    box.append(answer);
    return box;
  }
  if (state.kind === "sichuan") {
    const box = panel("牌河与副露");
    const log = el("div", "tile-log");
    const kinds = { pong: "碰", exposed_kong: "明杠", concealed_kong: "暗杠", added_kong: "补杠" };
    for (const seat of game.seats) {
      const row = el("div", "tile-log-row", el("strong", "", playerName(seat.id)));
      if (seat.melds.length) {
        row.append(el("div", "tile-log-rack", ...seat.melds.map((meld) => el("span", "meld",
          tileCard(meld.tile, "mini-tile"), kinds[meld.kind] || "杠"))));
      }
      if (seat.discards.length) {
        row.append(el("div", "tile-log-rack", ...seat.discards.map((value) => tileCard(value, "mini-tile"))));
      }
      if (!seat.melds.length && !seat.discards.length) row.append(el("p", "", "尚未出牌"));
      log.append(row);
    }
    box.append(log);
    return box;
  }
  const box = panel("桌上记录");
  if (!game.log?.length) return null;
  box.append(el("ol", "feed", ...[...(game.log || [])].reverse().map((entry) => el("li", "",
    entry.actor ? el("strong", "", `${playerName(entry.actor)} `) : null,
    entry.text, entry.target ? ` · ${playerName(entry.target)}` : null))));
  return box;
}

function rulesInfo(game) {
  const mode = currentMode();
  const descriptions = game?.rules || mode?.description || ({
    holdem: ["每人两张手牌，以任意五张组成最佳牌型。", "每手都有独立底池，可过牌、跟注、加注、全下或弃牌。", "最多五手，使用桌内筹码。"],
    sichuan: ["四人血战到底，先换三张再定缺。", "三家胡牌或牌墙用完结束，采用基础胡牌和杠分。"],
    kingdoms: ["主公身份公开，其余身份先保密。", "支持杀、闪、桃、摸牌锦囊、武器和濒死救援。", "主忠、反贼与内奸按各自目标争胜。"],
  })[state.kind];
  const lines = Array.isArray(descriptions) ? descriptions : [descriptions];
  const details = el("details", "panel rules", el("summary", "", "本桌规则"),
    el("ul", "", ...lines.map((text) => el("li", "", text))));
  details.id = "room-rules";
  details.open = rulesOpen;
  details.addEventListener("toggle", () => { if (details.isConnected) rulesOpen = details.open; });
  return details;
}

function holdemProgress(game) {
  if (state.kind !== "holdem" || game.finished) return null;
  const phases = ["preflop", "flop", "turn", "river"];
  const at = game.phase === "hand_end" ? phases.length : phases.indexOf(game.phase);
  const guide = panel("本手进程", el("span", "chip", `第 ${game.hand} 手`));
  guide.classList.add("round-guide");
  const list = el("ol", "round-steps");
  phases.forEach((phase, index) => {
    const step = el("li", index < at ? "passed" : index === at ? "here" : "", holdemPhases[phase]);
    if (index === at) step.setAttribute("aria-current", "step");
    list.append(step);
  });
  const ranks = el("details", "rank-guide", el("summary", "", "牌型顺序"),
    el("ol", "", ...["同花顺", "四条", "葫芦", "同花", "顺子", "三条", "两对", "一对", "高牌"]
      .map((rank) => el("li", "", rank))));
  guide.append(list, ranks);
  return guide;
}

function lobby(players) {
  const content = el("div", "lobby-main", lobbyBanner(players));
  if (players.length) {
    const roster = panel("已入座", el("span", "chip", `${players.length} / ${state.settings.capacity}`));
    roster.classList.add("lobby-roster");
    roster.append(el("div", "roster-grid", ...players.map((player, index) => seatLine({ id: player.player_id }, index, null))));
    content.append(roster);
  }
  return el("div", "room-shell lobby-layout", content,
    el("aside", "lobby-side", lobbyControls(players), applications(),
      el("p", "mode-description", `离开大厅会让出座位。断线超过 ${state.presence_timeout || 90} 秒也会释放大厅座位，桌主由在线玩家接任。`),
      rulesInfo(null)));
}

function match(game, players) {
  const main = el("div", "match-grid", table(game, players), handControls(game),
    el("aside", "board-feed", summary(game), holdemProgress(game), armies(game), tableUpdates(game), rulesInfo(game)));
  const ownTurn = state.seat === "seated" && game.actions.length > 0 && !game.finished;
  const turnLink = ownTurn ? el("a", "mobile-turn", el("span", "", "到你操作"), el("strong", "", "查看操作 ↑")) : null;
  if (turnLink) turnLink.href = "#hand-controls";
  return el("div", "room-shell match-layout", matchHeader(game), main, turnLink);
}

function render() {
  const active = document.activeElement;
  const activeField = active instanceof HTMLInputElement ? active.dataset.actionField : "";
  const cursor = activeField === "nickname" ? [active.selectionStart, active.selectionEnd] : null;
  const game = state.game;
  const players = state.players.filter((player) => player.seat === "seated");
  document.title = `${state.settings.name} · 缘缘庄牌桌`;
  roomMark.textContent = `桌号 ${state.room.slice(-6).toUpperCase()}`;
  roomMark.hidden = false;
  app.dataset.kind = state.kind;
  app.replaceChildren(game ? match(game, players) : lobby(players));
  if (activeField) {
    const next = app.querySelector(`[data-action-field="${activeField}"]`);
    next?.focus({ preventScroll: true });
    if (next && cursor) next.setSelectionRange(...cursor);
  }
}

async function watch() {
  await refresh();
  if (!stopped) setTimeout(watch, document.hidden ? 3300 : 850);
}

document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
window.addEventListener("online", refresh);
window.addEventListener("pageshow", () => refresh());
watch();
