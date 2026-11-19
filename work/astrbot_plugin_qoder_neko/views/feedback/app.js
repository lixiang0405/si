const bridge = window.AstrBotPluginView;
const tickets = document.getElementById("tickets");
const deployment = document.getElementById("deployment");
const status = document.getElementById("status");
const refresh = document.getElementById("refresh");

async function confirm(ticket, button) {
  button.disabled = true;
  try {
    await bridge.apiPost("feedback-release/confirm", { id: ticket.id, revision: ticket.revision });
    await load(`工单 #${ticket.id} 已确认完成。`);
  } catch {
    status.textContent = "确认未保存。请刷新工单后重试。";
    button.disabled = false;
  }
}

function renderTicket(ticket) {
  const article = document.createElement("article");
  const heading = document.createElement("div");
  heading.className = "ticket-heading";
  const title = document.createElement("h2");
  title.textContent = `#${ticket.id} · ${ticket.status}`;
  heading.append(title);
  if (ticket.can_confirm) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "confirm";
    button.textContent = "确认完成";
    button.addEventListener("click", () => confirm(ticket, button));
    heading.append(button);
  }
  article.append(heading);
  for (const text of [ticket.report, ticket.result]) {
    if (!text) continue;
    const content = document.createElement("p");
    content.className = "details";
    content.textContent = text;
    article.append(content);
  }
  return article;
}

async function load(message = "") {
  refresh.disabled = true;
  try {
    const data = await bridge.apiGet("feedback-release/tickets");
    deployment.textContent = data.deployment;
    tickets.replaceChildren(...data.tickets.map(renderTicket));
    status.textContent = message || (data.tickets.length ? "" : "暂时没有已结束的工单。");
  } catch {
    status.textContent = "无法读取工单。请确认 AstrBot 登录后刷新。";
  } finally {
    refresh.disabled = false;
  }
}

refresh.addEventListener("click", () => load());
load();
